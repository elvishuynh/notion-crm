// inbound email pipeline

const CONFIG = {
  SEARCH_QUERY: 'is:starred -label:CRM-SYNCED -label:CRM-Synced',
  BACKFILL_QUERY: 'is:starred',
  SYNCED_LABEL: 'CRM-SYNCED',
  DEFAULT_STATUS: 'Needs Review',
  GEMINI_MODEL: 'gemini-3.5-flash-lite', // lock model
  NOTION_API_VERSION: '2022-06-28',
  NOTION_BASE_URL: 'https://api.notion.com/v1',
  MAX_SNIPPET_LENGTH: 3000,
  NOTION_BLOCK_LIMIT: 2000
};

// scheduled trigger
function syncOpportunityEmails() {
  processEmails(CONFIG.SEARCH_QUERY, false);
}

// backfill
function resetAndBackfillAllOpportunities() {
  processEmails(CONFIG.BACKFILL_QUERY, true);
}

// test gemini connection
function testGeminiConnection() {
  const apiKey = PropertiesService.getScriptProperties().getProperty('GEMINI_API_KEY');
  Logger.log('Gemini API Key detected: ' + (apiKey ? 'YES (Length: ' + apiKey.length + ')' : 'NO / NULL'));

  if (!apiKey) {
    Logger.log('ERROR: Set GEMINI_API_KEY in Project Settings > Script Properties.');
    return;
  }

  const url = `https://generativelanguage.googleapis.com/v1beta/models/${CONFIG.GEMINI_MODEL}:generateContent?key=${apiKey}`;
  const response = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify({
      contents: [{ parts: [{ text: 'Respond strictly with JSON: {"status": "ok"}' }] }],
      generationConfig: { responseMimeType: 'application/json' }
    }),
    muteHttpExceptions: true
  });

  Logger.log('Gemini HTTP Status: ' + response.getResponseCode());
  Logger.log('Gemini Response Body: ' + response.getContentText());
}

// test notion connection
function testNotionConnection() {
  const notionApiKey = PropertiesService.getScriptProperties().getProperty('NOTION_API_KEY');
  const databaseId = PropertiesService.getScriptProperties().getProperty('NOTION_DATABASE_ID');

  Logger.log('Notion API Key: ' + (notionApiKey ? 'YES (Length: ' + notionApiKey.length + ')' : 'NO / NULL'));
  Logger.log('Notion Database ID: ' + (databaseId || 'NO / NULL'));

  if (!notionApiKey || !databaseId) {
    Logger.log('ERROR: NOTION_API_KEY and NOTION_DATABASE_ID must both be set in Script Properties.');
    return;
  }

  const cleanDbId = databaseId.replace(/-/g, '');
  const url = `${CONFIG.NOTION_BASE_URL}/databases/${cleanDbId}`;

  const res = callNotionApi(url, 'get', null, notionApiKey);
  if (res.error) {
    Logger.log(`ERROR connecting to Notion Database: [HTTP ${res.status}] ${res.message}`);
    return;
  }

  Logger.log(`SUCCESS! Connected to Notion Database: "${res.data.title?.[0]?.plain_text || 'Untitled'}"`);
  const propertyNames = Object.keys(res.data.properties || {});
  Logger.log(`Found ${propertyNames.length} properties: ${propertyNames.join(', ')}`);
}

// process email threads
function processEmails(searchQuery, isFullReset) {
  Logger.log(`Starting execution for query: "${searchQuery}" (Backfill: ${isFullReset})`);

  const notionApiKey = PropertiesService.getScriptProperties().getProperty('NOTION_API_KEY');
  const databaseId = PropertiesService.getScriptProperties().getProperty('NOTION_DATABASE_ID');
  const geminiApiKey = PropertiesService.getScriptProperties().getProperty('GEMINI_API_KEY');

  if (!notionApiKey || !databaseId) {
    Logger.log('FATAL: NOTION_API_KEY or NOTION_DATABASE_ID missing.');
    return;
  }

  const cleanDbId = databaseId.replace(/-/g, '');
  const syncedLabel = getOrCreateGmailLabel(CONFIG.SYNCED_LABEL);
  const myEmail = Session.getActiveUser().getEmail().toLowerCase();

  let threads = [];
  try {
    threads = GmailApp.search(searchQuery, 0, 50);
  } catch (err) {
    Logger.log('Error executing search query: ' + err.message);
    return;
  }

  if (!threads || threads.length === 0) {
    Logger.log('No matching threads found.');
    return;
  }

  let ingestedCount = 0;
  let skippedCount = 0;
  let deferredCount = 0;

  threads.forEach((thread, index) => {
    try {
      const threadId = thread.getId();
      const permalink = thread.getPermalink();

      Logger.log(`\n--- Inspecting Thread ${index + 1}/${threads.length} [ID: ${threadId}] ---`);

      // idempotency check
      const existsInNotion = isThreadInNotion(threadId, cleanDbId, notionApiKey);
      if (existsInNotion) {
        Logger.log(`Thread ${threadId} already exists in Notion. Tagging label and skipping.`);
        syncedLabel.addToThread(thread);
        skippedCount++;
        return;
      }

      const messages = thread.getMessages();
      if (!messages || messages.length === 0) return;

      const firstMessage = messages[0];
      const lastMessage = messages[messages.length - 1];

      const fromHeader = firstMessage.getFrom();
      const { name, email } = parseSender(fromHeader);
      const subject = (thread.getFirstMessageSubject() || '(No Subject)').trim();
      const fullSnippet = firstMessage.getPlainBody().substring(0, CONFIG.MAX_SNIPPET_LENGTH);

      // extract details with gemini
      const aiData = geminiApiKey ? extractOpportunityDetailsWithAI(subject, fullSnippet, email, geminiApiKey) : null;

      // defer thread on failure
      if (geminiApiKey && !aiData) {
        Logger.log(`[DEFERRED] Thread ID ${threadId} failed AI extraction after retries. Leaving for next run.`);
        deferredCount++;
        return;
      }

      const companyName = (aiData?.companyName || name || 'Unknown Company').trim();
      const companyDesc = (aiData?.companyDescription || '').trim();
      const oppType = aiData?.opportunityType || 'TBD / Discovery';

      // reply direction tracking
      const lastSender = parseSender(lastMessage.getFrom()).email.toLowerCase();
      const actionRequired = (lastSender === myEmail) ? 'Waiting on Reply' : 'Needs Reply';

      const threadStartDate = firstMessage.getDate().toISOString();
      const dateIngested = new Date().toISOString();

      // insert notion page
      const createRes = createNotionOpportunityPage({
        databaseId: cleanDbId,
        notionApiKey: notionApiKey,
        companyName: companyName,
        status: CONFIG.DEFAULT_STATUS,
        actionRequired: actionRequired,
        opportunityType: oppType,
        deliverableScope: 'TBD',
        senderName: name,
        senderEmail: email,
        companyDescription: companyDesc,
        threadStartDate: threadStartDate,
        dateIngested: dateIngested,
        threadLink: permalink,
        threadId: threadId,
        subject: subject,
        bodySnippet: fullSnippet
      });

      if (createRes.error) {
        Logger.log(`[ERROR] Failed to insert page for thread ${threadId}: ${createRes.message}`);
        return;
      }

      Logger.log(`[SUCCESS] Created Notion opportunity page for "${companyName}" [Page ID: ${createRes.id}]`);
      syncedLabel.addToThread(thread);
      ingestedCount++;

      // rate limit pacing
      Utilities.sleep(350);
    } catch (err) {
      Logger.log(`Exception processing thread ID ${thread.getId()}: ${err.message}`);
    }
  });

  Logger.log(`\nExecution complete. Ingested: ${ingestedCount}, Skipped: ${skippedCount}, Deferred: ${deferredCount}`);
}

// check existing thread id
function isThreadInNotion(threadId, databaseId, apiKey) {
  const url = `${CONFIG.NOTION_BASE_URL}/databases/${databaseId}/query`;
  const payload = {
    filter: {
      property: 'Thread ID',
      rich_text: { equals: threadId }
    },
    page_size: 1
  };

  const res = callNotionApi(url, 'post', payload, apiKey);
  if (res.error) {
    Logger.log(`Warning: Failed to query Notion for thread ID ${threadId}: ${res.message}`);
    return false;
  }

  return (res.data?.results && res.data.results.length > 0);
}

// build opportunity page payload
function createNotionOpportunityPage(params) {
  const url = `${CONFIG.NOTION_BASE_URL}/pages`;

  const properties = {
    'Company Name': {
      title: [{ text: { content: params.companyName.substring(0, 2000) } }]
    },
    'Status': {
      status: { name: params.status }
    },
    'Action Required': {
      select: { name: params.actionRequired }
    },
    'Opportunity Type': {
      select: { name: params.opportunityType }
    },
    'Deliverable Scope': {
      select: { name: params.deliverableScope }
    }
  };

  if (params.senderName) {
    properties['Sender Name'] = {
      rich_text: [{ text: { content: params.senderName.substring(0, 2000) } }]
    };
  }

  if (params.senderEmail) {
    properties['Sender Email'] = { email: params.senderEmail };
  }

  if (params.companyDescription) {
    properties['Company Description'] = {
      rich_text: [{ text: { content: params.companyDescription.substring(0, 2000) } }]
    };
  }

  properties['Thread ID'] = {
    rich_text: [{ text: { content: params.threadId.substring(0, 2000) } }]
  };

  if (params.threadLink) {
    properties['Thread Link'] = { url: params.threadLink };
  }

  properties['Thread Started'] = {
    date: { start: params.threadStartDate }
  };
  properties['Date Ingested'] = {
    date: { start: params.dateIngested }
  };

  // format body snippet blocks
  const children = [];
  const snippetChunks = chunkText(params.bodySnippet || '', CONFIG.NOTION_BLOCK_LIMIT);

  if (snippetChunks.length > 0) {
    children.push({
      object: 'block',
      type: 'heading_3',
      heading_3: {
        rich_text: [{ text: { content: `Original Thread: ${params.subject}`.substring(0, 2000) } }]
      }
    });

    snippetChunks.forEach(chunk => {
      children.push({
        object: 'block',
        type: 'callout',
        callout: {
          icon: { emoji: '📧' },
          rich_text: [{ text: { content: chunk } }]
        }
      });
    });
  }

  const payload = {
    parent: { database_id: params.databaseId },
    properties: properties,
    children: children
  };

  let res = callNotionApi(url, 'post', payload, params.notionApiKey);
  
  // fallback status
  if (res.error && res.message && res.message.includes('Invalid status option')) {
    Logger.log(`Status option "${params.status}" not found in Notion schema. Retrying with "Not started"...`);
    payload.properties['Status'] = { status: { name: 'Not started' } };
    res = callNotionApi(url, 'post', payload, params.notionApiKey);
    if (res.error && res.message && res.message.includes('Invalid status option')) {
      delete payload.properties['Status'];
      res = callNotionApi(url, 'post', payload, params.notionApiKey);
    }
  }

  if (res.error) {
    return { error: true, message: res.message };
  }

  return { id: res.data.id };
}

// execute notion api request
function callNotionApi(url, method, payload, apiKey) {
  const options = {
    method: method,
    headers: {
      'Authorization': `Bearer ${apiKey}`,
      'Notion-Version': CONFIG.NOTION_API_VERSION,
      'Content-Type': 'application/json'
    },
    muteHttpExceptions: true
  };

  if (payload) {
    options.payload = JSON.stringify(payload);
  }

  const maxRetries = 3;
  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    try {
      const response = UrlFetchApp.fetch(url, options);
      const code = response.getResponseCode();
      const rawText = response.getContentText();

      if ((code === 429 || code >= 500) && attempt < maxRetries) {
        Logger.log(`Notion API returned HTTP ${code}. Retrying in ${attempt * 2}s (Attempt ${attempt}/${maxRetries})...`);
        Utilities.sleep(attempt * 2000);
        continue;
      }

      if (code < 200 || code >= 300) {
        let errMsg = rawText;
        try {
          const parsed = JSON.parse(rawText);
          errMsg = parsed.message || rawText;
        } catch (_) {}
        return { error: true, status: code, message: errMsg };
      }

      const json = JSON.parse(rawText);
      return { error: false, status: code, data: json };
    } catch (e) {
      Logger.log(`Network exception calling Notion on attempt ${attempt}: ${e.message}`);
      if (attempt < maxRetries) {
        Utilities.sleep(attempt * 2000);
      } else {
        return { error: true, status: 0, message: e.message };
      }
    }
  }

  return { error: true, status: 0, message: 'Max retries exceeded' };
}

// prompt gemini model
function extractOpportunityDetailsWithAI(subject, body, senderEmail, apiKey) {
  const url = `https://generativelanguage.googleapis.com/v1beta/models/${CONFIG.GEMINI_MODEL}:generateContent?key=${apiKey}`;

  const prompt = `Analyze this inbound business email and extract key details:

Sender Email:
${senderEmail}

Subject:
${subject}

Body:
"""
${body}
"""

Return valid JSON with these fields:
"companyName": Name of the company/brand reaching out (infer from text, signature, or sender email domain).
"companyDescription": A concise 1-sentence description of what the company does or sells. STRICT RULE: DO NOT include or repeat the company name, brand name, or phrases like "This company" or "A brand that". Start directly with an action verb or noun phrase (e.g., instead of "OBSBOT makes AI cameras", write "Develops AI-powered webcams and tracking cameras").
"opportunityType": Choose ONLY one from this list: ["Paid Client", "Paid Integration", "Gifting / Product", "Affiliate", "TBD / Discovery"].`;

  const payload = {
    contents: [{ parts: [{ text: prompt }] }],
    generationConfig: {
      temperature: 0.1,
      responseMimeType: 'application/json'
    }
  };

  const options = {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  };

  const maxRetries = 3;
  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    try {
      const response = UrlFetchApp.fetch(url, options);
      const code = response.getResponseCode();
      const rawText = response.getContentText();

      // retry transient failures
      if ((code >= 500 || code === 429) && attempt < maxRetries) {
        Logger.log(`Gemini API returned HTTP ${code}. Retrying in ${attempt * 2}s... (Attempt ${attempt}/${maxRetries})`);
        Utilities.sleep(attempt * 2000);
        continue;
      }

      if (code !== 200) {
        Logger.log(`Gemini API Error (HTTP ${code}) on attempt ${attempt}: ${rawText}`);
        if (attempt < maxRetries) {
          Utilities.sleep(attempt * 2000);
          continue;
        }
        return null;
      }

      const json = JSON.parse(rawText);
      const contentText = json.candidates?.[0]?.content?.parts?.[0]?.text;
      if (!contentText) {
        Logger.log(`Gemini returned empty parts payload on attempt ${attempt}`);
        if (attempt < maxRetries) {
          Utilities.sleep(attempt * 2000);
          continue;
        }
        return null;
      }

      let parsed = null;
      try {
        parsed = JSON.parse(contentText);
      } catch (jsonErr) {
        const jsonMatch = contentText.match(/\{[\s\S]*\}/);
        if (jsonMatch) {
          try {
            parsed = JSON.parse(jsonMatch[0]);
          } catch (_) {}
        }
      }

      if (!parsed) {
        Logger.log(`Failed to parse valid JSON from Gemini output: ${contentText}`);
        if (attempt < maxRetries) {
          Utilities.sleep(attempt * 2000);
          continue;
        }
        return null;
      }

      return {
        companyName: parsed.companyName || '',
        companyDescription: parsed.companyDescription || '',
        opportunityType: parsed.opportunityType || 'TBD / Discovery'
      };

    } catch (networkErr) {
      Logger.log(`Network exception calling Gemini API on attempt ${attempt}: ${networkErr.message}`);
      if (attempt < maxRetries) {
        Utilities.sleep(attempt * 2000);
      } else {
        return null;
      }
    }
  }

  return null;
}

// chunk text for blocks
function chunkText(text, maxChars) {
  if (!text) return [];
  const chunks = [];
  let remaining = text;
  while (remaining.length > 0) {
    if (remaining.length <= maxChars) {
      chunks.push(remaining);
      break;
    }
    chunks.push(remaining.substring(0, maxChars));
    remaining = remaining.substring(maxChars);
  }
  return chunks;
}

// extract sender details
function parseSender(fromHeader) {
  if (!fromHeader) return { name: '', email: '' };
  const match = fromHeader.match(/^(.*?)\s*<(.+?)>$/);
  if (match) {
    let rawName = match[1].replace(/^["']|["']$/g, '').trim();
    const email = match[2].trim();
    return { name: rawName || email, email };
  }
  const clean = fromHeader.trim().replace(/^["']|["']$/g, '');
  return { name: clean, email: clean };
}

// get or create label
function getOrCreateGmailLabel(labelName) {
  let label = GmailApp.getUserLabelByName(labelName);
  if (!label) {
    label = GmailApp.createLabel(labelName);
  }
  return label;
}

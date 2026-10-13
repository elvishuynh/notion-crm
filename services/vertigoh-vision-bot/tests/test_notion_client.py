import unittest
from unittest.mock import patch, MagicMock
from notion_client import NotionClient


class TestNotionClient(unittest.TestCase):
    def setUp(self):
        self.client = NotionClient(
            api_key="ntn_fake_key",
            page_or_db_id="39a829eaf70480f5b919de007df824e7",
        )

    def test_extract_asset_link_url(self):
        props = {
            "Assets": {
                "type": "url",
                "url": "https://www.dropbox.com/s/sample123/video.mp4",
            }
        }
        link = self.client.extract_asset_link(props)
        self.assertEqual(link, "https://www.dropbox.com/s/sample123/video.mp4")

    def test_extract_asset_link_files_external(self):
        props = {
            "Assets": {
                "type": "files",
                "files": [
                    {
                        "type": "external",
                        "external": {"url": "https://drive.google.com/file/d/abc/view"},
                    }
                ],
            }
        }
        link = self.client.extract_asset_link(props)
        self.assertEqual(link, "https://drive.google.com/file/d/abc/view")

    def test_extract_asset_link_files_internal(self):
        props = {
            "Assets": {
                "type": "files",
                "files": [
                    {
                        "type": "file",
                        "file": {"url": "https://prod-files-secure.s3.us-west-2.amazonaws.com/xyz.mp4"},
                    }
                ],
            }
        }
        link = self.client.extract_asset_link(props)
        self.assertEqual(link, "https://prod-files-secure.s3.us-west-2.amazonaws.com/xyz.mp4")

    def test_extract_asset_link_rich_text(self):
        props = {
            "Assets": {
                "type": "rich_text",
                "rich_text": [
                    {
                        "plain_text": "Final Draft",
                        "href": "https://frame.io/player/123",
                    }
                ],
            }
        }
        link = self.client.extract_asset_link(props)
        self.assertEqual(link, "https://frame.io/player/123")

    def test_extract_asset_link_empty(self):
        props = {
            "Assets": {
                "type": "url",
                "url": None,
            }
        }
        self.assertIsNone(self.client.extract_asset_link(props))

    def test_extract_page_title(self):
        props = {
            "Name": {
                "type": "title",
                "title": [{"plain_text": "YouTube Shorts Episode 12"}],
            }
        }
        title = self.client.extract_page_title(props)
        self.assertEqual(title, "YouTube Shorts Episode 12")

    def test_extract_names_list_uploader_plural(self):
        # test extract uploaders plural
        props = {
            "Uploaders": {
                "type": "people",
                "people": [{"name": "Elvis"}, {"name": "Kai"}],
            }
        }
        names = self.client.extract_names_list(props, "uploader")
        self.assertEqual(names, ["Elvis", "Kai"])

    def test_extract_names_list_uploader_singular(self):
        # test extract uploader singular
        props = {
            "Uploader": {
                "type": "select",
                "select": {"name": "Roger"},
            }
        }
        names = self.client.extract_names_list(props, "uploader")
        self.assertEqual(names, ["Roger"])

    @patch.object(NotionClient, "_request")
    def test_query_assets_database(self, mock_request):
        self.client.database_id = "test_db_id"
        mock_request.return_value = {
            "results": [
                {
                    "id": "row_1",
                    "url": "https://notion.so/row_1",
                    "properties": {
                        "Name": {
                            "type": "title",
                            "title": [{"plain_text": "Podcast Clip Five"}],
                        },
                        "Assets": {
                            "type": "url",
                            "url": "https://vimeo.com/123456",
                        },
                        "Uploader": {
                            "type": "select",
                            "select": {"name": "Elvis"},
                        },
                    },
                },
                {
                    "id": "row_2",
                    "url": "https://notion.so/row_2",
                    "properties": {
                        "Name": {
                            "type": "title",
                            "title": [{"plain_text": "Draft without asset"}],
                        },
                        "Assets": {
                            "type": "url",
                            "url": None,
                        },
                    },
                },
            ],
            "has_more": False,
        }

        results = self.client.query_assets_database()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["notion_page_id"], "row_1")
        self.assertEqual(results[0]["page_title"], "Podcast Clip Five")
        self.assertEqual(results[0]["asset_url"], "https://vimeo.com/123456")
        self.assertEqual(results[0]["uploaders"], ["Elvis"])

    @patch.object(NotionClient, "_request")
    def test_update_page_status(self, mock_request):
        # test update page status
        mock_request.return_value = {"object": "page"}
        res = self.client.update_page_status("row_1", "Ready to Post")
        self.assertTrue(res)
        mock_request.assert_called_once_with(
            "PATCH",
            "/pages/row_1",
            json={"properties": {"Status": {"status": {"name": "Ready to Post"}}}},
        )


if __name__ == "__main__":
    unittest.main()

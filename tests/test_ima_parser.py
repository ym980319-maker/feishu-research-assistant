import unittest

from app.sources.ima_parser import parse_ima_home_response


SAMPLE_RESPONSE = {
    "code": 0,
    "list_rsp": {
        "is_end": True,
        "total_size": "2",
        "knowledge_base_info": {
            "id": "7357029865780274",
            "basic_info": {
                "name": "【爱分享】的财经资讯",
                "description": "测试知识库",
                "knowledge_total_size": "46963",
                "update_timestamp_sec": "1783937799718",
            },
            "member_info": {
                "member_count": "7244",
            },
        },
        "knowledge_list": [
            {
                "media_id": "note_test_001",
                "media_type": 11,
                "title": "测试笔记",
                "parent_folder_id": "7357029865780274",
                "update_time": "1783937799718",
                "media_type_info": {
                    "name": "笔记",
                },
            },
            {
                "media_id": "folder_7366826153504997",
                "media_type": 99,
                "title": "二、彭博社、路透社等外媒新闻",
                "media_type_info": {
                    "name": "文件夹",
                },
                "folder_info": {
                    "folder_id": "folder_7366826153504997",
                    "file_number": "7",
                    "folder_number": "6",
                },
            },
        ],
    },
}


class ImaParserTests(unittest.TestCase):

    def test_parse_success(self):
        page = parse_ima_home_response(SAMPLE_RESPONSE)

        self.assertEqual(
            page.knowledge_base.knowledge_base_id,
            "7357029865780274",
        )

        self.assertEqual(len(page.items), 2)

    def test_note_item(self):
        page = parse_ima_home_response(SAMPLE_RESPONSE)

        note = page.items[0]

        self.assertEqual(note.media_id, "note_test_001")
        self.assertEqual(note.media_type_name, "笔记")

    def test_folder_item(self):
        page = parse_ima_home_response(SAMPLE_RESPONSE)

        folder = page.items[1]

        self.assertEqual(
            folder.folder_id,
            "folder_7366826153504997",
        )

        self.assertEqual(folder.file_number, 7)
        self.assertEqual(folder.folder_number, 6)

    def test_error_code(self):
        with self.assertRaises(ValueError):
            parse_ima_home_response({"code": 500})

    def test_missing_list_rsp(self):
        with self.assertRaises(ValueError):
            parse_ima_home_response({"code": 0})


if __name__ == "__main__":
    unittest.main()

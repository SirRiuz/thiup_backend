# Python
from datetime import datetime

# Django
from django.test import Client, TransactionTestCase
from django.conf import settings
from rest_framework import status

# Libs
from apps.default.methods.tokens import encode_token


client = Client()


class ThreadsViewTest(TransactionTestCase):

    reset_sequences = True

    def __get_client_token(self) -> (str):
        """Se encarga de generar un client token."""
        payload = {"timestamp": datetime.now().__str__()}
        return encode_token(payload)

    def __decode_response_body(self, data):
        print("Helllo world")
        print(data)

    def test_get_threads(self):
        """
        This test aims to verify that, when trying to obtain
        the list of threads, the system is functioning correctly.
        """
        token = self.__get_client_token()
        response = client.get(
            "/threads/",
            HTTP_X_DYNAMIC_TOKEN=token
        )
        
        self.assertEquals(response.status_code, status.HTTP_200_OK)

    def test_create_thread(self):
        """
        This test is designed to verify that
        the creation of threads is
        functioning correctly.
        """
        token = self.__get_client_token()
        response = client.post(
            "/threads/",
            {
                "media": [],
                "text": "test",
                "content": {
                    "blocks": [
                        {
                            "key": "cmnci",
                            "text": "test",
                            "type": "unstyled",
                            "depth": 0,
                            "inlineStyleRanges": [],
                            "entityRanges": [],
                            "data": {},
                        }
                    ],
                    "entityMap": {},
                },
            },
            content_type="application/json",
            HTTP_X_DYNAMIC_TOKEN=token
        )
        self.assertEquals(response.status_code, status.HTTP_201_CREATED)

    def test_create_and_retrieve_thread(self):
        """
        This test verifies that the retrieval
        of threads is functioning correctly.
        """
        token = self.__get_client_token()
        thread = client.post(
            "/threads/",
            {
                "media": [],
                "text": "test",
                "content": {
                    "blocks": [
                        {
                            "key": "cmnci",
                            "text": "test",
                            "type": "unstyled",
                            "depth": 0,
                            "inlineStyleRanges": [],
                            "entityRanges": [],
                            "data": {},
                        }
                    ],
                    "entityMap": {},
                },
            },
            content_type="application/json",
            HTTP_X_DYNAMIC_TOKEN=token
        )

        token = self.__get_client_token()
        response = client.get(
            f"/threads/{thread.data['short_id']}/",
            HTTP_X_DYNAMIC_TOKEN=token
        )

        self.assertEquals(response.status_code, status.HTTP_200_OK)

    def test_retrieve_thread_with_fail_id(self):
        """
        Verifies that when attempting to retrieve a thread
        with an invalid identifier, the flow
        functions correctly.
        """
        token = self.__get_client_token()
        response = client.get(
            f"/threads/.../",
            HTTP_X_DYNAMIC_TOKEN=token
        )
        self.assertEquals(
            response.status_code, status.HTTP_404_NOT_FOUND)

    def test_create_thread_without_required_fields(self):
        """
        Verifies that when attempting to create a thread
        without the necessary fields, the
        flow functions correctly.
        """
        token = self.__get_client_token()
        response = client.post(
            "/threads/",
            {
                "media": [],
                "text": "..."
            },
            content_type="application/json",
            HTTP_X_DYNAMIC_TOKEN=token
        )
        self.assertEquals(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_search_thread_by_keyword(self):
        """
        Verifies that when attempting to search for a thread
        with a keyword, it is functioning correctly.
        """
        token = self.__get_client_token()
        response = client.post(
            "/threads/",
            {
                "media": [],
                "text": "test",
                "content": {
                    "blocks": [
                        {
                            "key": "cmnci",
                            "text": "test",
                            "type": "unstyled",
                            "depth": 0,
                            "inlineStyleRanges": [],
                            "entityRanges": [],
                            "data": {},
                        }
                    ],
                    "entityMap": {},
                },
            },
            content_type="application/json",
            HTTP_X_DYNAMIC_TOKEN=token
        )
        token = self.__get_client_token()
        threads = client.get(
            "/threads/?q=test",
            HTTP_X_DYNAMIC_TOKEN=token)
            
        self.assertTrue(bool(threads.data["count"]))

    def test_search_thread_by_tag(self):
        """
        Verifies that when attempting to search for a
        thread with a tag name, it is functioning correctly.
        """
        token = self.__get_client_token()
        response = client.post(
            "/threads/",
            {
                "media": [],
                "text": "test #test",
                "content": {
                    "blocks": [
                        {
                            "key": "cmnci",
                            "text": "test #test",
                            "type": "unstyled",
                            "depth": 0,
                            "inlineStyleRanges": [],
                            "entityRanges": [],
                            "data": {},
                        }
                    ],
                    "entityMap": {},
                },
            },
            content_type="application/json",
            HTTP_X_DYNAMIC_TOKEN=token
        )
        token = self.__get_client_token()
        threads = client.get("/threads/?tag=test", HTTP_X_DYNAMIC_TOKEN=token)
        #self.__decode_response_body(threads.data)
        self.assertTrue(bool(threads.data["count"]))

    def test_create_sub_thread(self):
        """
        This test verifies that the thread response system
        is functioning correctly.
        """
        token = self.__get_client_token()
        thread = client.post(
            "/threads/",
            {
                "media": [],
                "text": "test #test",
                "content": {
                    "blocks": [
                        {
                            "key": "cmnci",
                            "text": "test #test",
                            "type": "unstyled",
                            "depth": 0,
                            "inlineStyleRanges": [],
                            "entityRanges": [],
                            "data": {},
                        }
                    ],
                    "entityMap": {},
                },
            },
            HTTP_X_DYNAMIC_TOKEN=token,
            content_type="application/json",
        )

        token = self.__get_client_token()
        sub_thread = client.post(
            "/threads/",
            {
                "sub": thread.data["id"],
                "media": [],
                "text": "test",
                "content": {
                    "blocks": [
                        {
                            "key": "51p2k",
                            "text": "test",
                            "type": "unstyled",
                            "depth": 0,
                            "inlineStyleRanges": [],
                            "entityRanges": [],
                            "data": {}
                        }
                    ],
                    "entityMap": {}
                }
            },
            HTTP_X_DYNAMIC_TOKEN=token,
            content_type="application/json",
        )

        self.assertEquals(sub_thread.status_code, status.HTTP_201_CREATED)

    def test_get_threads_responses(self):
        """
        This test verifies that when attempting to
        obtain the responses of a thread, the flow
        is functioning correctly.
        """
        token = self.__get_client_token()
        thread = client.post(
            "/threads/",
            {
                "media": [],
                "text": "test #test",
                "content": {
                    "blocks": [
                        {
                            "key": "cmnci",
                            "text": "test #test",
                            "type": "unstyled",
                            "depth": 0,
                            "inlineStyleRanges": [],
                            "entityRanges": [],
                            "data": {},
                        }
                    ],
                    "entityMap": {},
                },
            },
            content_type="application/json",
            HTTP_X_DYNAMIC_TOKEN=token
        )
        sub_thread = client.post(
            "/threads/",
            {
                "sub": thread.data["id"],
                "media": [],
                "text": "test",
                "content": {
                    "blocks": [
                        {
                            "key": "51p2k",
                            "text": "test",
                            "type": "unstyled",
                            "depth": 0,
                            "inlineStyleRanges": [],
                            "entityRanges": [],
                            "data": {}
                        }
                    ],
                    "entityMap": {}
                }
            },
            content_type="application/json",
            HTTP_X_DYNAMIC_TOKEN=token
        )

        response = client.get(f"/threads/{thread.data['id']}/responses/")
        self.assertTrue(response.status_code, status.HTTP_200_OK)

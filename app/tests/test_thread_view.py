# Python
from datetime import datetime

# Django
from django.core.cache import cache
from django.test import Client, TransactionTestCase, override_settings
from rest_framework import status

# Libs
from app.methods.tokens import encode_token

client = Client()


# These CRUD/search tests use plain JSON bodies, so the transport layers are
# pinned off: ENCRYPTED_RESPONSE=False (no encrypted-body enforcement) and
# SINGLE_REQUEST_PROTECT=False (no Client-assertion ticket required). The
# encrypted/ticket paths are covered in test_foryou.py.
@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class ThreadsViewTest(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        # The create throttle counts per IP in the LocMem cache, which
        # outlives each test in the pytest process: clear it so creates from
        # previous tests/files never bleed a 429 into this one.
        cache.clear()

    def __get_client_token(self) -> str:
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
        response = client.get("/threads/", HTTP_X_DYNAMIC_TOKEN=token)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

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
            HTTP_X_DYNAMIC_TOKEN=token,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

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
            HTTP_X_DYNAMIC_TOKEN=token,
        )

        token = self.__get_client_token()
        response = client.get(f"/threads/{thread.data['uid']}/", HTTP_X_DYNAMIC_TOKEN=token)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_thread_with_fail_id(self):
        """
        Verifies that when attempting to retrieve a thread
        with an invalid identifier, the flow
        functions correctly.
        """
        token = self.__get_client_token()
        response = client.get("/threads/.../", HTTP_X_DYNAMIC_TOKEN=token)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_create_thread_without_required_fields(self):
        """
        Verifies that when attempting to create a thread
        without the necessary fields, the
        flow functions correctly.
        """
        token = self.__get_client_token()
        response = client.post(
            "/threads/", {"media": [], "text": "..."}, content_type="application/json", HTTP_X_DYNAMIC_TOKEN=token
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

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
            HTTP_X_DYNAMIC_TOKEN=token,
        )
        token = self.__get_client_token()
        threads = client.get("/threads/?q=test", HTTP_X_DYNAMIC_TOKEN=token)

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
            HTTP_X_DYNAMIC_TOKEN=token,
        )
        token = self.__get_client_token()
        threads = client.get("/threads/?tag=test", HTTP_X_DYNAMIC_TOKEN=token)
        # self.__decode_response_body(threads.data)
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
                "sub": thread.data["uid"],
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
                            "data": {},
                        }
                    ],
                    "entityMap": {},
                },
            },
            HTTP_X_DYNAMIC_TOKEN=token,
            content_type="application/json",
        )

        self.assertEqual(sub_thread.status_code, status.HTTP_201_CREATED)

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
            HTTP_X_DYNAMIC_TOKEN=token,
        )
        sub_thread = client.post(
            "/threads/",
            {
                "sub": thread.data["uid"],
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
                            "data": {},
                        }
                    ],
                    "entityMap": {},
                },
            },
            content_type="application/json",
            HTTP_X_DYNAMIC_TOKEN=token,
        )

        response = client.get(f"/threads/{thread.data['uid']}/responses/")
        self.assertTrue(response.status_code, status.HTTP_200_OK)

    def __create_thread(self, text, sub=None):
        """Creates a thread (or a reply when `sub` is given) and returns the
        response. Minimal valid DraftJS content, no media."""
        token = self.__get_client_token()
        body = {
            "media": [],
            "text": text,
            "content": {
                "blocks": [
                    {
                        "key": "cmnci",
                        "text": text,
                        "type": "unstyled",
                        "depth": 0,
                        "inlineStyleRanges": [],
                        "entityRanges": [],
                        "data": {},
                    }
                ],
                "entityMap": {},
            },
        }
        if sub:
            body["sub"] = sub
        return client.post(
            "/threads/",
            body,
            content_type="application/json",
            HTTP_X_DYNAMIC_TOKEN=token,
        )

    def test_responses_of_reply_includes_parent_chain(self):
        """
        Comment permalink contract: GET /threads/<uid>/responses/ where the
        uid is a REPLY resolves the reply as the head and exposes its
        ancestor chain in `parents` (root first, immediate parent last), so
        the client can render the Threads-style thread continuity. A root
        thread answers with an empty `parents`.
        """
        root = self.__create_thread("root thread")
        reply = self.__create_thread("first level reply", sub=root.data["uid"])
        sub_reply = self.__create_thread("second level reply", sub=reply.data["uid"])

        # Root thread → no ancestors.
        response = client.get(f"/threads/{root.data['uid']}/responses/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["head"]["uid"], root.data["uid"])
        self.assertEqual(response.data["parents"], [])

        # First-level reply → chain is [root].
        response = client.get(f"/threads/{reply.data['uid']}/responses/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["head"]["uid"], reply.data["uid"])
        self.assertEqual([p["uid"] for p in response.data["parents"]], [root.data["uid"]])
        # Its page lists the sub-reply as a response.
        self.assertEqual([r["uid"] for r in response.data["results"]], [sub_reply.data["uid"]])

        # Second-level reply → chain is [root, reply], root FIRST.
        response = client.get(f"/threads/{sub_reply.data['uid']}/responses/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["head"]["uid"], sub_reply.data["uid"])
        self.assertEqual(
            [p["uid"] for p in response.data["parents"]],
            [root.data["uid"], reply.data["uid"]],
        )
        # All test threads share the same mask (same client IP), so every
        # node reads as authored by the thread's OP — the boolean contract.
        self.assertTrue(all(p["is_op"] for p in response.data["parents"]))

    def test_text_length_gate_is_threads_exact(self):
        """Server-side text limit (Threads' 500, posts and replies alike):
        exactly 500 chars → 201; 501 → 400. The FE composers mirror it, but
        the API is the real gate."""
        at_limit = self.__create_thread("x" * 500)
        self.assertEqual(at_limit.status_code, status.HTTP_201_CREATED)

        over = self.__create_thread("x" * 501)
        self.assertEqual(over.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("text", over.data)

        # Replies share the same gate.
        over_reply = self.__create_thread("x" * 501, sub=at_limit.data["uid"])
        self.assertEqual(over_reply.status_code, status.HTTP_400_BAD_REQUEST)

    def test_responses_parent_chain_truncates_on_hidden_ancestor(self):
        """
        A soft-deleted ancestor TRUNCATES the chain at that point (the
        permalink still resolves, with less context) — hidden content never
        rides inside `parents`.
        """
        # Local import (test-only): flip the middle ancestor directly in DB.
        from app.models.thread import Thread

        root = self.__create_thread("root thread")
        reply = self.__create_thread("first level reply", sub=root.data["uid"])
        sub_reply = self.__create_thread("second level reply", sub=reply.data["uid"])

        Thread.objects.filter(uid=reply.data["uid"]).update(is_active=False)

        response = client.get(f"/threads/{sub_reply.data['uid']}/responses/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # The walk stops at the hidden parent: no ancestors survive (the
        # root is only reachable THROUGH the hidden node).
        self.assertEqual(response.data["parents"], [])

# Python
from datetime import datetime

# Django
from django.test import Client, TransactionTestCase, override_settings
from rest_framework import status

# Models
from app.models.reaction import Reaction
from app.models.reaction_relation import ReactionRelation

# Libs
from app.methods.tokens import encode_token


# New reaction set (6) — internal id in `name`, emoji is presentation.
NEW_REACTIONS = [
    ("love", "❤️"),
    ("laugh", "😂"),
    ("wow", "😮"),
    ("sad", "😢"),
    ("angry", "😡"),
    ("applause", "👏"),
]

client = Client()


# These CRUD tests use plain JSON bodies, so the transport layers are pinned
# off: ENCRYPTED_RESPONSE=False (no encrypted-body enforcement) and
# SINGLE_REQUEST_PROTECT=False (no Client-assertion ticket required). The
# encrypted/ticket paths are covered in test_foryou.py.
@override_settings(ENCRYPTED_RESPONSE=False, SINGLE_REQUEST_PROTECT=False)
class ThreadsViewTest(TransactionTestCase):

    reset_sequences = True

    def setUp(self):
        # Reset to a known catalog (the data migration already seeds the 6, and
        # `name` is unique — clear first to avoid clashing across tests).
        ReactionRelation.objects.all().delete()
        Reaction.objects.all().delete()
        for name, emoji in NEW_REACTIONS:
            Reaction.objects.create(name=name, emoji=emoji)

    def __get_client_token(self) -> (str):
        """Se encarga de generar un client token."""
        payload = {"timestamp": datetime.now().__str__()}
        return encode_token(payload)

    def test_get_reactions(self):
        """
        This test verifies that when attempting to obtain
        the list of reactions, it is functioning correctly.
        """
        token = self.__get_client_token()
        response = client.get(
            "/reactions/",
            HTTP_X_DYNAMIC_TOKEN=token)

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK)

    def test_asing_reaction_to_thread(self):
        """
        This test verifies that when assigning a reaction
        to a thread, everything is functioning correctly.
        """
        token = self.__get_client_token()
        reaction = Reaction.objects.filter(
            is_active=True).\
                order_by("?").first()

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
            HTTP_X_DYNAMIC_TOKEN=token,
            content_type="application/json",
        )

        token = self.__get_client_token()
        reaction = client.post(
            "/reactions/",
            {
                "reaction": reaction.id,
                "thread": thread.data["uid"]
            },
            HTTP_X_DYNAMIC_TOKEN=token,
            content_type="application/json"
        )

        self.assertEqual(
            reaction.status_code,
            status.HTTP_201_CREATED)

    def test_asing_and_delete_reaction_to_thread(self):
        """
        This test will create and delete a reaction
        from a thread.
        """
        token = self.__get_client_token()
        reaction = Reaction.objects.filter(
            is_active=True).\
                order_by("?").first()

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
            HTTP_X_DYNAMIC_TOKEN=token,
            content_type="application/json",
        )
        
        token = self.__get_client_token()
        response = client.post(
            "/reactions/",
            {
                "reaction": reaction.id,
                "thread": thread.data["uid"]
            },
            HTTP_X_DYNAMIC_TOKEN=token,
            content_type="application/json"
        )

        token = self.__get_client_token()
        response = client.post(
            "/reactions/",
            {
                "reaction": reaction.id,
                "thread": thread.data["uid"]
            },
            HTTP_X_DYNAMIC_TOKEN=token,
            content_type="application/json"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED)

    def __make_thread(self) -> (str):
        """Create a thread and return its uid."""
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
            HTTP_X_DYNAMIC_TOKEN=token,
            content_type="application/json",
        )
        return thread.data["uid"]

    def __react(self, reaction_id, thread_uid):
        token = self.__get_client_token()
        return client.post(
            "/reactions/",
            {"reaction": reaction_id, "thread": thread_uid},
            HTTP_X_DYNAMIC_TOKEN=token,
            content_type="application/json",
        )

    def test_one_reaction_per_user(self):
        """Switching reactions keeps EXACTLY ONE reaction per user per thread:
        reacting with A then B (different) leaves a single relation, now B."""
        first, second = Reaction.objects.all()[:2]
        uid = self.__make_thread()

        self.__react(first.id, uid)
        response = self.__react(second.id, uid)

        relations = ReactionRelation.objects.filter(thread__uid=uid)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(relations.count(), 1)
        self.assertEqual(relations.first().reaction_id, second.id)

    def test_reaction_count(self):
        """A single reaction yields reaction_count == 1 in the response."""
        reaction = Reaction.objects.first()
        uid = self.__make_thread()

        response = self.__react(reaction.id, uid)

        match = next(
            (r for r in response.data["reactions"] if r["name"] == reaction.name),
            None,
        )
        self.assertIsNotNone(match)
        self.assertEqual(match["reaction_count"], 1)

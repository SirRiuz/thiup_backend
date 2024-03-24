# Python
from datetime import datetime

# Django
from django.test import Client, TransactionTestCase
from rest_framework import status

# Models
from apps.reactions.models.reaction import Reaction

# Libs
from apps.default.methods.tokens import encode_token


client = Client()


class ThreadsViewTest(TransactionTestCase):

    reset_sequences = True

    def setUp(self):
        Reaction.objects.create(name="foo-1")
        Reaction.objects.create(name="foo-2")
        Reaction.objects.create(name="foo-3")

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

        self.assertEquals(
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
            "/api/threads/",
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
                "thread": thread.data["id"]
            },
            HTTP_X_DYNAMIC_TOKEN=token,
            content_type="application/json"
        )

        self.assertEquals(
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
            "/api/threads/",
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
                "thread": thread.data["id"]
            },
            HTTP_X_DYNAMIC_TOKEN=token,
            content_type="application/json"
        )

        token = self.__get_client_token()
        response = client.post(
            "/reactions/",
            {
                "reaction": reaction.id,
                "thread": thread.data["id"]
            },
            HTTP_X_DYNAMIC_TOKEN=token,
            content_type="application/json"
        )

        self.assertEquals(
            response.status_code,
            status.HTTP_201_CREATED)

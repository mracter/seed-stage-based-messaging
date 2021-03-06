import responses
import json

try:
    from urllib.parse import urlparse
except ImportError:
    from urlparse import urlparse

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.db.models.signals import post_save
from django.conf import settings

from rest_framework import status
from rest_framework.test import APIClient
from rest_framework.authtoken.models import Token
from requests_testadapter import TestAdapter, TestSession
from go_http.metrics import MetricsApiClient

from .models import (Subscription, fire_sub_action_if_new,
                     disable_schedule_if_complete,
                     disable_schedule_if_deactivated, fire_metrics_if_new)
from contentstore.models import Schedule, MessageSet, BinaryContent, Message
from .tasks import (schedule_create, schedule_disable, fire_metric,
                    scheduled_metrics)
from . import tasks


class RecordingAdapter(TestAdapter):

    """ Record the request that was handled by the adapter.
    """
    request = None

    def send(self, request, *args, **kw):
        self.request = request
        return super(RecordingAdapter, self).send(request, *args, **kw)


class APITestCase(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.adminclient = APIClient()
        self.session = TestSession()


class AuthenticatedAPITestCase(APITestCase):

    def make_schedule(self):
        # Create hourly schedule
        schedule_data = {
            'hour': 1
        }
        return Schedule.objects.create(**schedule_data)

    def make_messageset(self):
        messageset_data = {
            'short_name': 'messageset_one',
            'notes': None,
            'next_set': None,
            'default_schedule': self.schedule,
            'content_type': 'text'
        }
        return MessageSet.objects.create(**messageset_data)

    def make_messageset_audio(self):
        messageset_data = {
            'short_name': 'messageset_two',
            'notes': None,
            'next_set': None,
            'default_schedule': self.schedule,
            'content_type': 'audio'
        }
        return MessageSet.objects.create(**messageset_data)

    def make_subscription(self):
        post_data = {
            "identity": "8646b7bc-b511-4965-a90b-e1145e398703",
            "messageset": self.messageset,
            "next_sequence_number": 1,
            "lang": "en_ZA",
            "active": True,
            "completed": False,
            "schedule": self.schedule,
            "process_status": 0,
            "metadata": {
                "source": "RapidProVoice"
            }
        }
        return Subscription.objects.create(**post_data)

    def make_subscription_welcome(self):
        post_data = {
            "identity": "8646b7bc-b511-4965-a90b-e1145e398703",
            "messageset": self.messageset,
            "next_sequence_number": 1,
            "lang": "en_ZA",
            "active": True,
            "completed": False,
            "schedule": self.schedule,
            "process_status": 0,
            "metadata": {
                "prepend_next_delivery": "Welcome to your messages!"
            }
        }
        return Subscription.objects.create(**post_data)

    def make_subscription_audio(self):
        post_data = {
            "identity": "8646b7bc-b511-4965-a90b-e1145e398703",
            "messageset": self.messageset_audio,
            "next_sequence_number": 1,
            "lang": "en_ZA",
            "active": True,
            "completed": False,
            "schedule": self.schedule,
            "process_status": 0,
            "metadata": {
                "source": "RapidProVoice"
            }
        }
        return Subscription.objects.create(**post_data)

    def make_subscription_audio_welcome(self):
        post_data = {
            "identity": "8646b7bc-b511-4965-a90b-e1145e398703",
            "messageset": self.messageset_audio,
            "next_sequence_number": 1,
            "lang": "en_ZA",
            "active": True,
            "completed": False,
            "schedule": self.schedule,
            "process_status": 0,
            "metadata": {
                "prepend_next_delivery": "http://example.com/welcome.mp3"
            }
        }
        return Subscription.objects.create(**post_data)

    def _replace_get_metric_client(self, session=None):
        return MetricsApiClient(
            auth_token=settings.METRICS_AUTH_TOKEN,
            api_url=settings.METRICS_URL,
            session=self.session)

    def _restore_get_metric_client(session=None):
        return MetricsApiClient(
            auth_token=settings.METRICS_AUTH_TOKEN,
            api_url=settings.METRICS_URL,
            session=session)

    def _replace_post_save_hooks(self):
        def has_listeners():
            return post_save.has_listeners(Subscription)
        assert has_listeners(), (
            "Subscription model has no post_save listeners. Make sure"
            " helpers cleaned up properly in earlier tests.")
        post_save.disconnect(fire_sub_action_if_new, sender=Subscription)
        post_save.disconnect(disable_schedule_if_complete, sender=Subscription)
        post_save.disconnect(disable_schedule_if_deactivated,
                             sender=Subscription)
        post_save.disconnect(fire_metrics_if_new, sender=Subscription)
        assert not has_listeners(), (
            "Subscription model still has post_save listeners. Make sure"
            " helpers cleaned up properly in earlier tests.")

    def _restore_post_save_hooks(self):
        def has_listeners():
            return post_save.has_listeners(Subscription)
        assert not has_listeners(), (
            "Subscription model still has post_save listeners. Make sure"
            " helpers removed them properly in earlier tests.")
        post_save.connect(fire_sub_action_if_new, sender=Subscription)
        post_save.connect(disable_schedule_if_complete, sender=Subscription)
        post_save.connect(disable_schedule_if_deactivated, sender=Subscription)
        post_save.connect(fire_metrics_if_new, sender=Subscription)

    def setUp(self):
        super(AuthenticatedAPITestCase, self).setUp()

        self._replace_post_save_hooks()
        tasks.get_metric_client = self._replace_get_metric_client

        self.username = 'testuser'
        self.password = 'testpass'
        self.user = User.objects.create_user(self.username,
                                             'testuser@example.com',
                                             self.password)
        token = Token.objects.create(user=self.user)
        self.token = token.key
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token)
        self.schedule = self.make_schedule()
        self.messageset = self.make_messageset()
        self.messageset_audio = self.make_messageset_audio()
        self.superuser = User.objects.create_superuser('testsu',
                                                       'su@example.com',
                                                       'dummypwd')
        sutoken = Token.objects.create(user=self.superuser)
        self.adminclient.credentials(
            HTTP_AUTHORIZATION='Token %s' % sutoken)

    def tearDown(self):
        self._restore_post_save_hooks()
        tasks.get_metric_client = self._restore_get_metric_client()


class TestLogin(AuthenticatedAPITestCase):

    def test_login(self):
        # Setup
        post_auth = {"username": "testuser",
                     "password": "testpass"}
        # Execute
        request = self.client.post(
            '/api/token-auth/', post_auth)
        token = request.data.get('token', None)
        # Check
        self.assertIsNotNone(
            token, "Could not receive authentication token on login post.")
        self.assertEqual(
            request.status_code, 200,
            "Status code on /api/token-auth was %s (should be 200)."
            % request.status_code)


class TestSubscriptionsAPI(AuthenticatedAPITestCase):

    def test_create_subscription_data(self):
        # Setup
        post_subscription = {
            "identity": "7646b7bc-b511-4965-a90b-e1145e398703",
            "messageset": self.messageset.id,
            "next_sequence_number": 1,
            "lang": "en_ZA",
            "active": True,
            "completed": False,
            "schedule": self.schedule.id,
            "process_status": 0,
            "metadata": {
                "source": "RapidProVoice"
            }
        }
        # Execute
        response = self.client.post('/api/v1/subscriptions/',
                                    json.dumps(post_subscription),
                                    content_type='application/json')
        # Check
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        d = Subscription.objects.last()
        self.assertIsNotNone(d.id)
        self.assertEqual(d.version, 1)
        self.assertEqual(d.messageset.id, self.messageset.id)
        self.assertEqual(d.next_sequence_number, 1)
        self.assertEqual(d.lang, "en_ZA")
        self.assertEqual(d.active, True)
        self.assertEqual(d.completed, False)
        self.assertEqual(d.schedule.id, self.schedule.id)
        self.assertEqual(d.process_status, 0)
        self.assertEqual(d.metadata["source"], "RapidProVoice")

    def test_read_subscription_data(self):
        # Setup
        existing = self.make_subscription()
        # Execute
        response = self.client.get('/api/v1/subscriptions/%s/' % existing.id,
                                   content_type='application/json')
        # Check
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        d = Subscription.objects.last()
        self.assertIsNotNone(d.id)
        self.assertEqual(d.version, 1)
        self.assertEqual(d.messageset.id, self.messageset.id)
        self.assertEqual(d.next_sequence_number, 1)
        self.assertEqual(d.lang, "en_ZA")
        self.assertEqual(d.active, True)
        self.assertEqual(d.completed, False)
        self.assertEqual(d.schedule.id, self.schedule.id)
        self.assertEqual(d.process_status, 0)
        self.assertEqual(d.metadata["source"], "RapidProVoice")

    def test_filter_subscription_data(self):
        # Setup
        sub_active = self.make_subscription()
        sub_inactive = self.make_subscription()
        sub_inactive.active = False
        sub_inactive.save()
        # Precheck
        self.assertEqual(sub_active.active, True)
        self.assertEqual(sub_inactive.active, False)
        # Execute
        response = self.client.get(
            '/api/v1/subscriptions/',
            {"identity": "8646b7bc-b511-4965-a90b-e1145e398703",
             "active": "True"},
            content_type='application/json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], str(sub_active.id))

    def test_update_subscription_data(self):
        # Setup
        existing = self.make_subscription()
        patch_subscription = {
            "next_sequence_number": 10,
            "active": False,
            "completed": True
        }
        # Execute
        response = self.client.patch('/api/v1/subscriptions/%s/' % existing.id,
                                     json.dumps(patch_subscription),
                                     content_type='application/json')
        # Check
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        d = Subscription.objects.get(pk=existing.id)
        self.assertEqual(d.active, False)
        self.assertEqual(d.completed, True)
        self.assertEqual(d.next_sequence_number, 10)
        self.assertEqual(d.lang, "en_ZA")

    def test_delete_subscription_data(self):
        # Setup
        existing = self.make_subscription()
        # Execute
        response = self.client.delete(
            '/api/v1/subscriptions/%s/' % existing.id,
            content_type='application/json')
        # Check
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        d = Subscription.objects.filter(id=existing.id).count()
        self.assertEqual(d, 0)


class TestCreateScheduleTask(AuthenticatedAPITestCase):

    @responses.activate
    def test_create_schedule_task(self):
        # Setup
        # make schedule
        schedule_data = {
            "minute": "1",
            "hour": "6",
            "day_of_week": "1",
            "day_of_month": "*",
            "month_of_year": "*"
        }
        schedule = Schedule.objects.create(**schedule_data)

        # make messageset
        messageset_data = {
            "short_name": "pregnancy",
            "notes": "Base pregancy set",
            "next_set": None,
            "default_schedule": schedule
        }
        messageset = MessageSet.objects.create(**messageset_data)

        # make binarycontent
        binarycontent_data1 = {
            "content": "fakefilename",
        }
        binarycontent1 = BinaryContent.objects.create(**binarycontent_data1)
        binarycontent_data2 = {
            "content": "fakefilename",
        }
        binarycontent2 = BinaryContent.objects.create(**binarycontent_data2)

        # make messages
        message_data1 = {
            "messageset": messageset,
            "sequence_number": 1,
            "lang": "en_ZA",
            "binary_content": binarycontent1,
        }
        Message.objects.create(**message_data1)
        message_data2 = {
            "messageset": messageset,
            "sequence_number": 2,
            "lang": "en_ZA",
            "binary_content": binarycontent2,
        }
        Message.objects.create(**message_data2)

        # make subscription
        post_data = {
            "identity": "8646b7bc-b511-4965-a90b-e1145e398703",
            "messageset": messageset,
            "next_sequence_number": 1,
            "lang": "en_ZA",
            "active": True,
            "completed": False,
            "schedule": schedule,
            "process_status": 0,
            "metadata": {
                "source": "RapidProVoice"
            }
        }
        existing = Subscription.objects.create(**post_data)

        # Create schedule
        schedule_post = {
            "id": "6455245a-028b-4fa1-82fc-6b639c4e7710",
            "cron_definition": "1 6 1 * *",
            "endpoint": "%s/%s/%s/send" % (
                "http://seed-stage-based-messaging/api/v1",
                "subscription",
                str(existing.id)),
            "frequency": None,
            "messages": None,
            "triggered": 0,
            "created_at": "2015-04-05T21:59:28Z",
            "updated_at": "2015-04-05T21:59:28Z"
        }
        responses.add(responses.POST,
                      "http://seed-scheduler/api/v1/schedule/",
                      json.dumps(schedule_post),
                      status=200, content_type='application/json')

        result = schedule_create.apply_async(args=[str(existing.id)])
        self.assertEqual(
            str(result.get()), "6455245a-028b-4fa1-82fc-6b639c4e7710")

        d = Subscription.objects.get(pk=existing.id)
        self.assertIsNotNone(d.id)
        self.assertEqual(
            d.metadata["scheduler_schedule_id"],
            "6455245a-028b-4fa1-82fc-6b639c4e7710")

    @responses.activate
    def test_disable_schedule_task(self):
        # Setup
        subscription = self.make_subscription()
        schedule_id = "6455245a-028b-4fa1-82fc-6b639c4e7710"
        subscription.metadata["scheduler_schedule_id"] = schedule_id
        subscription.save()

        # mock schedule update
        responses.add(
            responses.PATCH,
            "http://seed-scheduler/api/v1/schedule/%s/" % schedule_id,
            json.dumps({"enabled": False}),
            status=200, content_type='application/json')

        # Execute
        result = schedule_disable.apply_async(args=[str(subscription.id)])

        # Check
        self.assertEqual(result.get(), True)
        self.assertEqual(len(responses.calls), 1)


class TestSubscriptionsWebhookListener(AuthenticatedAPITestCase):

    def test_webhook_subscription_data_good(self):
        # Setup
        post_webhook = {
            "hook": {
                "id": 5,
                "event": "subscriptionrequest.added",
                "target": "http://example.com/api/v1/subscriptions/request"
            },
            "data": {
                "messageset": self.messageset.id,
                "updated_at": "2016-02-17T07:59:42.831568+00:00",
                "identity": "7646b7bc-b511-4965-a90b-e1145e398703",
                "lang": "en_ZA",
                "created_at": "2016-02-17T07:59:42.831533+00:00",
                "id": "5282ed58-348f-4a54-b1ff-f702e36ec3cc",
                "next_sequence_number": 1,
                "schedule": self.schedule.id
            }
        }
        # Execute
        response = self.client.post('/api/v1/subscriptions/request',
                                    json.dumps(post_webhook),
                                    content_type='application/json')
        # Check
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        d = Subscription.objects.last()
        self.assertIsNotNone(d.id)
        self.assertEqual(d.version, 1)
        self.assertEqual(d.messageset.id, self.messageset.id)
        self.assertEqual(d.next_sequence_number, 1)
        self.assertEqual(d.lang, "en_ZA")
        self.assertEqual(d.active, True)
        self.assertEqual(d.completed, False)
        self.assertEqual(d.schedule.id, self.schedule.id)
        self.assertEqual(d.process_status, 0)

    def test_webhook_subscription_data_bad(self):
        # Setup with missing identity
        post_webhook = {
            "hook": {
                "id": 5,
                "event": "subscriptionrequest.added",
                "target": "http://example.com/api/v1/subscriptions/request"
            },
            "data": {
                "messageset": self.messageset.id,
                "updated_at": "2016-02-17T07:59:42.831568+00:00",
                "lang": "en_ZA",
                "created_at": "2016-02-17T07:59:42.831533+00:00",
                "id": "5282ed58-348f-4a54-b1ff-f702e36ec3cc",
                "next_sequence_number": 1,
                "schedule": self.schedule.id
            }
        }
        # Execute
        response = self.client.post('/api/v1/subscriptions/request',
                                    json.dumps(post_webhook),
                                    content_type='application/json')
        # Check
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json(),
                         {"identity": ["This field is required."]})


class TestSendMessageTask(AuthenticatedAPITestCase):

    @responses.activate
    def test_send_message_task_to_mother_text(self):
        post_save.connect(fire_sub_action_if_new, sender=Subscription)
        # mock schedule sending
        responses.add(
            responses.POST,
            "http://seed-scheduler/api/v1/schedule/",
            json={
                "id": "1234"
            },
            status=201, content_type='application/json'
        )
        # Setup
        existing = self.make_subscription()

        # Precheck
        subs_all = Subscription.objects.all()
        self.assertEqual(subs_all.count(), 1)
        scheds_all = Schedule.objects.all()
        self.assertEqual(scheds_all.count(), 1)

        # mock identity address lookup
        responses.add(
            responses.GET,
            "http://seed-identity-store/api/v1/identities/%s/addresses/msisdn?default=True" % (existing.identity, ),  # noqa
            json={
                "count": 1,
                "next": None,
                "previous": None,
                "results": [{"address": "+2345059992222"}]
            },
            status=200, content_type='application/json',
            match_querystring=True
        )

        # mock identity address lookup
        responses.add(
            responses.GET,
            "http://seed-identity-store/api/v1/identities/%s/" % (
                existing.identity, ),
            json={
                "id": existing.identity,
                "version": 1,
                "details": {
                    "default_addr_type": "msisdn",
                    "addresses": {
                        "msisdn": {
                            "+2345059992222": {}
                        }
                    },
                    "receiver_role": "mother",
                    "linked_to": None,
                    "preferred_msg_type": "text",
                    "preferred_language": "en_ZA"
                },
                "created_at": "2015-07-10T06:13:29.693272Z",
                "updated_at": "2015-07-10T06:13:29.693298Z"
            },
            status=200, content_type='application/json',
        )

        # Create message sender call
        responses.add(
            responses.POST,
            "http://seed-message-sender/api/v1/outbound/",
            json={
                "url": "http://seed-message-sender/api/v1/outbound/c7f3c839-2bf5-42d1-86b9-ccb886645fb4/",  # noqa
                "id": "c7f3c839-2bf5-42d1-86b9-ccb886645fb4",
                "version": 1,
                "to_addr": "+2345059992222",
                "vumi_message_id": None,
                "content": "This is message 1",
                "delivered": False,
                "attempts": 0,
                "metadata": {},
                "created_at": "2016-03-24T13:43:43.614952Z",
                "updated_at": "2016-03-24T13:43:43.614921Z"
            },
            status=200, content_type='application/json'
        )

        # make messages
        message_data_eng_1 = {
            "messageset": existing.messageset,
            "sequence_number": 1,
            "lang": "en_ZA",
            "text_content": "This is message 1",
        }
        Message.objects.create(**message_data_eng_1)
        message_data_eng_2 = {
            "messageset": existing.messageset,
            "sequence_number": 2,
            "lang": "en_ZA",
            "text_content": "This is message 2",
        }
        Message.objects.create(**message_data_eng_2)
        message_data_eng_3 = {
            "messageset": existing.messageset,
            "sequence_number": 3,
            "lang": "en_ZA",
            "text_content": "This is message 3",
        }
        Message.objects.create(**message_data_eng_3)
        message_data_zul_1 = {
            "messageset": existing.messageset,
            "sequence_number": 1,
            "lang": "zu_ZA",
            "text_content": "Ke msg 1",
        }
        Message.objects.create(**message_data_zul_1)
        message_data_zul_2 = {
            "messageset": existing.messageset,
            "sequence_number": 2,
            "lang": "zu_ZA",
            "text_content": "Ke msg 2",
        }
        Message.objects.create(**message_data_zul_2)
        message_data_zul_3 = {
            "messageset": existing.messageset,
            "sequence_number": 3,
            "lang": "zu_ZA",
            "text_content": "Ke msg 3",
        }
        Message.objects.create(**message_data_zul_3)

        # Execute
        response = self.client.post('/api/v1/subscriptions/%s/send' % (
            existing.id, ), content_type='application/json')
        # Check
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        d = Subscription.objects.get(id=existing.id)
        self.assertEqual(d.version, 1)
        self.assertEqual(d.messageset.id, self.messageset.id)
        self.assertEqual(d.next_sequence_number, 2)
        self.assertEqual(d.active, True)
        self.assertEqual(d.completed, False)
        self.assertEqual(d.process_status, 0)
        subs_all = Subscription.objects.all()
        self.assertEqual(subs_all.count(), 1)
        scheds_all = Schedule.objects.all()
        self.assertEqual(scheds_all.count(), 1)
        self.assertEqual(len(responses.calls), 4)

        # Check the message_count / set_max count
        message_count = existing.messageset.messages.filter(
            lang=existing.lang).count()
        self.assertEqual(message_count, 3)

        post_save.disconnect(fire_sub_action_if_new, sender=Subscription)

    @responses.activate
    def test_send_message_task_to_mother_text_welcome(self):
        # Setup
        existing = self.make_subscription_welcome()

        # mock identity address lookup
        responses.add(
            responses.GET,
            "http://seed-identity-store/api/v1/identities/%s/addresses/msisdn?default=True" % (existing.identity, ),  # noqa
            json={
                "count": 1,
                "next": None,
                "previous": None,
                "results": [{"address": "+2345059992222"}]
            },
            status=200, content_type='application/json',
            match_querystring=True
        )

        # mock identity address lookup
        responses.add(
            responses.GET,
            "http://seed-identity-store/api/v1/identities/%s/" % (
                existing.identity, ),
            json={
                "id": existing.identity,
                "version": 1,
                "details": {
                    "default_addr_type": "msisdn",
                    "addresses": {
                        "msisdn": {
                            "+2345059992222": {}
                        }
                    },
                    "receiver_role": "mother",
                    "linked_to": None,
                    "preferred_msg_type": "text",
                    "preferred_language": "en_ZA"
                },
                "created_at": "2015-07-10T06:13:29.693272Z",
                "updated_at": "2015-07-10T06:13:29.693298Z"
            },
            status=200, content_type='application/json',
        )

        # Create message sender call
        responses.add(
            responses.POST,
            "http://seed-message-sender/api/v1/outbound/",
            json={
                "url": "http://seed-message-sender/api/v1/outbound/c7f3c839-2bf5-42d1-86b9-ccb886645fb4/",  # noqa
                "id": "c7f3c839-2bf5-42d1-86b9-ccb886645fb4",
                "version": 1,
                "to_addr": "+2345059992222",
                "vumi_message_id": None,
                "content": "Welcome to your messages!\nThis is message 1",
                "delivered": False,
                "attempts": 0,
                "metadata": {},
                "created_at": "2016-03-24T13:43:43.614952Z",
                "updated_at": "2016-03-24T13:43:43.614921Z"
            },
            status=200, content_type='application/json'
        )

        # make messages
        message_data1 = {
            "messageset": existing.messageset,
            "sequence_number": 1,
            "lang": "en_ZA",
            "text_content": "This is message 1",
        }
        Message.objects.create(**message_data1)
        message_data2 = {
            "messageset": existing.messageset,
            "sequence_number": 2,
            "lang": "en_ZA",
            "text_content": "This is message 2",
        }
        Message.objects.create(**message_data2)

        # Execute
        response = self.client.post('/api/v1/subscriptions/%s/send' % (
            existing.id, ), content_type='application/json')
        # Check
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        d = Subscription.objects.get(id=existing.id)
        self.assertEqual(d.version, 1)
        self.assertEqual(d.messageset.id, self.messageset.id)
        self.assertEqual(d.next_sequence_number, 2)
        self.assertEqual(d.active, True)
        self.assertEqual(d.completed, False)
        self.assertEqual(d.process_status, 0)
        self.assertEqual(d.metadata["prepend_next_delivery"], None)

    @responses.activate
    def test_send_message_task_to_mother_text_last(self):
        # Setup
        post_save.connect(disable_schedule_if_complete, sender=Subscription)
        schedule_id = "6455245a-028b-4fa1-82fc-6b639c4e7710"
        existing = self.make_subscription()
        existing.metadata["scheduler_schedule_id"] = schedule_id
        existing.next_sequence_number = 2  # fast forward to end
        existing.save()

        # mock identity address lookup
        responses.add(
            responses.GET,
            "http://seed-identity-store/api/v1/identities/%s/addresses/msisdn?default=True" % (existing.identity, ),  # noqa
            json={
                "count": 1,
                "next": None,
                "previous": None,
                "results": [{"address": "+2345059992222"}]
            },
            status=200, content_type='application/json',
            match_querystring=True
        )

        # mock identity address lookup
        responses.add(
            responses.GET,
            "http://seed-identity-store/api/v1/identities/%s/" % (
                existing.identity, ),
            json={
                "id": existing.identity,
                "version": 1,
                "details": {
                    "default_addr_type": "msisdn",
                    "addresses": {
                        "msisdn": {
                            "+2345059992222": {}
                        }
                    },
                    "receiver_role": "mother",
                    "linked_to": None,
                    "preferred_msg_type": "text",
                    "preferred_language": "en_ZA"
                },
                "created_at": "2015-07-10T06:13:29.693272Z",
                "updated_at": "2015-07-10T06:13:29.693298Z"
            },
            status=200, content_type='application/json',
        )

        # mock message sender call
        responses.add(
            responses.POST,
            "http://seed-message-sender/api/v1/outbound/",
            json={
                "url": "http://seed-message-sender/api/v1/outbound/c7f3c839-2bf5-42d1-86b9-ccb886645fb4/",  # noqa
                "id": "c7f3c839-2bf5-42d1-86b9-ccb886645fb4",
                "version": 1,
                "to_addr": "+2345059992222",
                "vumi_message_id": None,
                "content": "This is message 2",
                "delivered": False,
                "attempts": 0,
                "metadata": {},
                "created_at": "2016-03-24T13:43:43.614952Z",
                "updated_at": "2016-03-24T13:43:43.614921Z"
            },
            status=200, content_type='application/json'
        )

        # mock schedule update
        responses.add(
            responses.PATCH,
            "http://seed-scheduler/api/v1/schedule/%s/" % schedule_id,
            json.dumps({"enabled": False}),
            status=200, content_type='application/json')

        # make messages
        message_data1 = {
            "messageset": existing.messageset,
            "sequence_number": 1,
            "lang": "en_ZA",
            "text_content": "This is message 1",
        }
        Message.objects.create(**message_data1)
        message_data2 = {
            "messageset": existing.messageset,
            "sequence_number": 2,
            "lang": "en_ZA",
            "text_content": "This is message 2",
        }
        Message.objects.create(**message_data2)

        # Execute
        response = self.client.post('/api/v1/subscriptions/%s/send' % (
            existing.id, ), content_type='application/json')
        # Check
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        d = Subscription.objects.get(id=existing.id)
        self.assertEqual(d.version, 1)
        self.assertEqual(d.messageset.id, self.messageset.id)
        self.assertEqual(d.next_sequence_number, 2)
        self.assertEqual(d.active, False)
        self.assertEqual(d.completed, True)
        self.assertEqual(d.process_status, 2)
        self.assertEqual(len(responses.calls), 4)

        post_save.disconnect(disable_schedule_if_complete, sender=Subscription)

    @responses.activate
    def test_send_message_task_to_mother_text_in_process(self):
        # Setup
        existing = self.make_subscription()
        existing.process_status = 1
        existing.save()

        # Precheck for comparison
        self.assertEqual(existing.next_sequence_number, 1)
        subs_all = Subscription.objects.all()
        self.assertEqual(subs_all.count(), 1)
        scheds_all = Schedule.objects.all()
        self.assertEqual(scheds_all.count(), 1)

        # make messages
        message_data_eng_1 = {
            "messageset": existing.messageset,
            "sequence_number": 1,
            "lang": "en_ZA",
            "text_content": "This is message 1",
        }
        Message.objects.create(**message_data_eng_1)
        message_data_eng_2 = {
            "messageset": existing.messageset,
            "sequence_number": 2,
            "lang": "en_ZA",
            "text_content": "This is message 2",
        }
        Message.objects.create(**message_data_eng_2)
        message_data_eng_3 = {
            "messageset": existing.messageset,
            "sequence_number": 3,
            "lang": "en_ZA",
            "text_content": "This is message 3",
        }
        Message.objects.create(**message_data_eng_3)
        message_data_zul_1 = {
            "messageset": existing.messageset,
            "sequence_number": 1,
            "lang": "zu_ZA",
            "text_content": "Ke msg 1",
        }
        Message.objects.create(**message_data_zul_1)
        message_data_zul_2 = {
            "messageset": existing.messageset,
            "sequence_number": 2,
            "lang": "zu_ZA",
            "text_content": "Ke msg 2",
        }
        Message.objects.create(**message_data_zul_2)
        message_data_zul_3 = {
            "messageset": existing.messageset,
            "sequence_number": 3,
            "lang": "zu_ZA",
            "text_content": "Ke msg 3",
        }
        Message.objects.create(**message_data_zul_3)

        # Execute
        response = self.client.post('/api/v1/subscriptions/%s/send' % (
            existing.id, ), content_type='application/json')
        # Check
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        d = Subscription.objects.get(id=existing.id)
        self.assertEqual(d.version, 1)
        self.assertEqual(d.messageset.id, self.messageset.id)
        self.assertEqual(d.next_sequence_number, 1)
        self.assertEqual(d.active, True)
        self.assertEqual(d.completed, False)
        self.assertEqual(d.process_status, 1)
        subs_all = Subscription.objects.all()
        self.assertEqual(subs_all.count(), 1)
        scheds_all = Schedule.objects.all()
        self.assertEqual(scheds_all.count(), 1)
        self.assertEqual(len(responses.calls), 0)

        post_save.disconnect(fire_sub_action_if_new, sender=Subscription)

    @responses.activate
    def test_send_message_task_to_other_text(self):
        # Setup
        existing = self.make_subscription()

        # mock identity address lookup
        responses.add(
            responses.GET,
            "http://seed-identity-store/api/v1/identities/%s/addresses/msisdn?default=True" % (  # noqa
                "3f7c8851-5204-43f7-af7f-005059993333", ),
            json={
                "count": 1,
                "next": None,
                "previous": None,
                "results": [{"address": "+2345059993333"}]
            },
            status=200, content_type='application/json',
            match_querystring=True
        )

        # mock identity address lookup
        responses.add(
            responses.GET,
            "http://seed-identity-store/api/v1/identities/%s/" % (
                existing.identity, ),
            json={
                "id": existing.identity,
                "version": 1,
                "details": {
                    "default_addr_type": "msisdn",
                    "addresses": {
                        "msisdn": {
                            "+2345059992222": {}
                        }
                    },
                    "receiver_role": "mother",
                    "linked_to": None,
                    "preferred_msg_type": "text",
                    "preferred_language": "en_ZA"
                },
                "communicate_through": "3f7c8851-5204-43f7-af7f-005059993333",
                "created_at": "2015-07-10T06:13:29.693272Z",
                "updated_at": "2015-07-10T06:13:29.693298Z"
            },
            status=200, content_type='application/json',
        )

        # mock identity address lookup - friend
        responses.add(
            responses.GET,
            "http://seed-identity-store/api/v1/identities/%s/" % (
                "3f7c8851-5204-43f7-af7f-005059993333", ),
            json={
                "id": "3f7c8851-5204-43f7-af7f-005059993333",
                "version": 1,
                "details": {
                    "default_addr_type": "msisdn",
                    "addresses": {
                        "msisdn": {
                            "+2345059993333": {}
                        }
                    },
                    "receiver_role": "friend",
                    "linked_to": existing.identity,
                    "preferred_msg_type": "text",
                    "preferred_language": "en_ZA"
                },
                "created_at": "2015-07-10T06:13:29.693272Z",
                "updated_at": "2015-07-10T06:13:29.693298Z"
            },
            status=200, content_type='application/json',
        )

        # Create message sender call
        responses.add(
            responses.POST,
            "http://seed-message-sender/api/v1/outbound/",
            json={
                "url": "http://seed-message-sender/api/v1/outbound/c7f3c839-2bf5-42d1-86b9-ccb886645fb4/",  # noqa
                "id": "c7f3c839-2bf5-42d1-86b9-ccb886645fb4",
                "version": 1,
                "to_addr": "+2345059993333",
                "vumi_message_id": None,
                "content": "This is message 1",
                "delivered": False,
                "attempts": 0,
                "metadata": {},
                "created_at": "2016-03-24T13:43:43.614952Z",
                "updated_at": "2016-03-24T13:43:43.614921Z"
            },
            status=200, content_type='application/json'
        )

        # make messages
        message_data1 = {
            "messageset": existing.messageset,
            "sequence_number": 1,
            "lang": "en_ZA",
            "text_content": "This is message 1",
        }
        Message.objects.create(**message_data1)
        message_data2 = {
            "messageset": existing.messageset,
            "sequence_number": 2,
            "lang": "en_ZA",
            "text_content": "This is message 2",
        }
        Message.objects.create(**message_data2)

        # Execute
        response = self.client.post('/api/v1/subscriptions/%s/send' % (
            existing.id, ), content_type='application/json')
        # Check
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        d = Subscription.objects.get(id=existing.id)
        self.assertEqual(d.version, 1)
        self.assertEqual(d.messageset.id, self.messageset.id)
        self.assertEqual(d.next_sequence_number, 2)
        self.assertEqual(d.active, True)
        self.assertEqual(d.completed, False)
        self.assertEqual(d.process_status, 0)

    @responses.activate
    def test_send_message_task_to_mother_audio(self):
        # Setup
        existing = self.make_subscription_audio()

        # mock identity address lookup
        responses.add(
            responses.GET,
            "http://seed-identity-store/api/v1/identities/%s/addresses/msisdn?default=True" % (existing.identity, ),  # noqa
            json={
                "count": 1,
                "next": None,
                "previous": None,
                "results": [{"address": "+2345059992222"}]
            },
            status=200, content_type='application/json',
            match_querystring=True
        )

        # mock identity address lookup
        responses.add(
            responses.GET,
            "http://seed-identity-store/api/v1/identities/%s/" % (
                existing.identity, ),
            json={
                "id": existing.identity,
                "version": 1,
                "details": {
                    "default_addr_type": "msisdn",
                    "addresses": {
                        "msisdn": {
                            "+2345059992222": {}
                        }
                    },
                    "receiver_role": "mother",
                    "linked_to": None,
                    "preferred_msg_type": "audio",
                    "preferred_msg_days": "mon_wed",
                    "preferred_msg_times": "2_5",
                    "preferred_language": "en_ZA"
                },
                "created_at": "2015-07-10T06:13:29.693272Z",
                "updated_at": "2015-07-10T06:13:29.693298Z"
            },
            status=200, content_type='application/json',
        )

        # Create message sender call
        responses.add(
            responses.POST,
            "http://seed-message-sender/api/v1/outbound/",
            json={
                "url": "http://seed-message-sender/api/v1/outbound/c7f3c839-2bf5-42d1-86b9-ccb886645fb4/",  # noqa
                "id": "c7f3c839-2bf5-42d1-86b9-ccb886645fb4",
                "version": 1,
                "to_addr": "+2345059992222",
                "vumi_message_id": None,
                "content": None,
                "delivered": False,
                "attempts": 0,
                "metadata": {
                    'voice_speech_url': 'fakefilename.mp3'
                },
                "created_at": "2016-03-24T13:43:43.614952Z",
                "updated_at": "2016-03-24T13:43:43.614921Z"
            },
            status=200, content_type='application/json'
        )

        # make binarycontent
        binarycontent_data1 = {
            "content": "fakefilename.mp3",
        }
        binarycontent1 = BinaryContent.objects.create(**binarycontent_data1)
        binarycontent_data2 = {
            "content": "fakefilename.mp3",
        }
        binarycontent2 = BinaryContent.objects.create(**binarycontent_data2)

        # make messages
        message_data1 = {
            "messageset": existing.messageset,
            "sequence_number": 1,
            "lang": "en_ZA",
            "binary_content": binarycontent1,
        }
        Message.objects.create(**message_data1)
        message_data2 = {
            "messageset": existing.messageset,
            "sequence_number": 2,
            "lang": "en_ZA",
            "binary_content": binarycontent2,
        }
        Message.objects.create(**message_data2)

        # Execute
        response = self.client.post('/api/v1/subscriptions/%s/send' % (
            existing.id, ), content_type='application/json')
        # Check
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        d = Subscription.objects.get(id=existing.id)
        self.assertEqual(d.version, 1)
        self.assertEqual(d.next_sequence_number, 2)
        self.assertEqual(d.active, True)
        self.assertEqual(d.completed, False)
        self.assertEqual(d.process_status, 0)

    @responses.activate
    def test_send_message_task_to_mother_audio_first_with_welcome(self):
        # Setup
        existing = self.make_subscription_audio_welcome()

        # mock identity address lookup
        responses.add(
            responses.GET,
            "http://seed-identity-store/api/v1/identities/%s/addresses/msisdn?default=True" % (existing.identity, ),  # noqa
            json={
                "count": 1,
                "next": None,
                "previous": None,
                "results": [{"address": "+2345059992222"}]
            },
            status=200, content_type='application/json',
            match_querystring=True
        )

        # mock identity address lookup
        responses.add(
            responses.GET,
            "http://seed-identity-store/api/v1/identities/%s/" % (
                existing.identity, ),
            json={
                "id": existing.identity,
                "version": 1,
                "details": {
                    "default_addr_type": "msisdn",
                    "addresses": {
                        "msisdn": {
                            "+2345059992222": {}
                        }
                    },
                    "receiver_role": "mother",
                    "linked_to": None,
                    "preferred_msg_type": "audio",
                    "preferred_msg_days": "mon_wed",
                    "preferred_msg_times": "2_5",
                    "preferred_language": "en_ZA"
                },
                "created_at": "2015-07-10T06:13:29.693272Z",
                "updated_at": "2015-07-10T06:13:29.693298Z"
            },
            status=200, content_type='application/json',
        )

        # Create message sender call
        responses.add(
            responses.POST,
            "http://seed-message-sender/api/v1/outbound/",
            json={
                "url": "http://seed-message-sender/api/v1/outbound/c7f3c839-2bf5-42d1-86b9-ccb886645fb4/",  # noqa
                "id": "c7f3c839-2bf5-42d1-86b9-ccb886645fb4",
                "version": 1,
                "to_addr": "+2345059992222",
                "vumi_message_id": None,
                "content": None,
                "delivered": False,
                "attempts": 0,
                "metadata": {
                    'voice_speech_url': [
                        'http://example.com/welcome.mp3', 'fakefilename.mp3'
                    ]
                },
                "created_at": "2016-03-24T13:43:43.614952Z",
                "updated_at": "2016-03-24T13:43:43.614921Z"
            },
            status=200, content_type='application/json'
        )

        # make binarycontent
        binarycontent_data1 = {
            "content": "fakefilename.mp3",
        }
        binarycontent1 = BinaryContent.objects.create(**binarycontent_data1)
        binarycontent_data2 = {
            "content": "fakefilename.mp3",
        }
        binarycontent2 = BinaryContent.objects.create(**binarycontent_data2)

        # make messages
        message_data1 = {
            "messageset": existing.messageset,
            "sequence_number": 1,
            "lang": "en_ZA",
            "binary_content": binarycontent1,
        }
        Message.objects.create(**message_data1)
        message_data2 = {
            "messageset": existing.messageset,
            "sequence_number": 2,
            "lang": "en_ZA",
            "binary_content": binarycontent2,
        }
        Message.objects.create(**message_data2)

        # Execute
        response = self.client.post('/api/v1/subscriptions/%s/send' % (
            existing.id, ), content_type='application/json')
        # Check
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        d = Subscription.objects.get(id=existing.id)
        self.assertEqual(d.version, 1)
        self.assertEqual(d.next_sequence_number, 2)
        self.assertEqual(d.active, True)
        self.assertEqual(d.completed, False)
        self.assertEqual(d.process_status, 0)
        self.assertEqual(d.metadata["prepend_next_delivery"], None)

    @override_settings(USE_SSL=True)
    def test_make_absolute_url(self):
        self.assertEqual(
            tasks.make_absolute_url('foo'),
            'https://example.com/foo')
        self.assertEqual(
            tasks.make_absolute_url('/foo'),
            'https://example.com/foo')

    @override_settings(USE_SSL=False)
    def test_make_absolute_url_ssl(self):
        self.assertEqual(
            tasks.make_absolute_url('foo'),
            'http://example.com/foo')
        self.assertEqual(
            tasks.make_absolute_url('/foo'),
            'http://example.com/foo')


class TestDeactivateSubscription(AuthenticatedAPITestCase):

    @responses.activate
    def test_deactivation_deactivates_schedule(self):
        # Setup
        post_save.connect(disable_schedule_if_deactivated, sender=Subscription)
        schedule_id = "6455245a-028b-4fa1-82fc-6b639c4e7710"
        sub = self.make_subscription()
        sub.metadata["scheduler_schedule_id"] = schedule_id
        sub.save()

        # mock schedule update
        responses.add(
            responses.PATCH,
            "http://seed-scheduler/api/v1/schedule/%s/" % schedule_id,
            json.dumps({"enabled": False}),
            status=200, content_type='application/json')

        # Execute
        sub.active = False
        sub.save()
        # Check
        self.assertEqual(len(responses.calls), 1)
        post_save.disconnect(disable_schedule_if_deactivated,
                             sender=Subscription)


class TestMetricsAPI(AuthenticatedAPITestCase):

    def test_metrics_read(self):
        # Setup
        # Execute
        response = self.client.get('/api/metrics/',
                                   content_type='application/json')
        # Check
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["metrics_available"], [
                'subscriptions.created.sum',
                'subscriptions.send_next_message_errored.sum',
                'subscriptions.active.last',
                'subscriptions.created.last',
                'subscriptions.broken.last',
                'subscriptions.completed.last',
                'subscriptions.messageset_one.active.last',
                'subscriptions.messageset_two.active.last'
            ]
        )

    @responses.activate
    def test_post_metrics(self):
        # Setup
        # deactivate Testsession for this test
        self.session = None
        responses.add(responses.POST,
                      "http://metrics-url/metrics/",
                      json={"foo": "bar"},
                      status=200, content_type='application/json')
        # Execute
        response = self.client.post('/api/metrics/',
                                    content_type='application/json')
        # Check
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["scheduled_metrics_initiated"], True)


class TestMetrics(AuthenticatedAPITestCase):

    def check_request(
            self, request, method, params=None, data=None, headers=None):
        self.assertEqual(request.method, method)
        if params is not None:
            url = urlparse.urlparse(request.url)
            qs = urlparse.parse_qsl(url.query)
            self.assertEqual(dict(qs), params)
        if headers is not None:
            for key, value in headers.items():
                self.assertEqual(request.headers[key], value)
        if data is None:
            self.assertEqual(request.body, None)
        else:
            self.assertEqual(json.loads(request.body), data)

    def _mount_session(self):
        response = [{
            'name': 'foo',
            'value': 9000,
            'aggregator': 'bar',
        }]
        adapter = RecordingAdapter(json.dumps(response).encode('utf-8'))
        self.session.mount(
            "http://metrics-url/metrics/", adapter)
        return adapter

    def test_direct_fire(self):
        # Setup
        adapter = self._mount_session()
        # Execute
        result = fire_metric.apply_async(kwargs={
            "metric_name": 'foo.last',
            "metric_value": 1,
            "session": self.session
        })
        # Check
        self.check_request(
            adapter.request, 'POST',
            data={"foo.last": 1.0}
        )
        self.assertEqual(result.get(),
                         "Fired metric <foo.last> with value <1.0>")

    def test_created_metrics(self):
        # Setup
        adapter = self._mount_session()
        # reconnect metric post_save hook
        post_save.connect(fire_metrics_if_new, sender=Subscription)

        # Execute
        self.make_subscription()

        # Check
        self.check_request(
            adapter.request, 'POST',
            data={"subscriptions.created.sum": 1.0}
        )
        # remove post_save hooks to prevent teardown errors
        post_save.disconnect(fire_metrics_if_new, sender=Subscription)

    @responses.activate
    def test_multiple_created_metrics(self):
        # Setup
        # deactivate Testsession for this test
        self.session = None
        # reconnect metric post_save hook
        post_save.connect(fire_metrics_if_new, sender=Subscription)
        # add metric post response
        responses.add(responses.POST,
                      "http://metrics-url/metrics/",
                      json={"foo": "bar"},
                      status=200, content_type='application/json')

        # Execute
        self.make_subscription()
        self.make_subscription()

        # Check
        self.assertEqual(len(responses.calls), 2)
        # remove post_save hooks to prevent teardown errors
        post_save.disconnect(fire_metrics_if_new, sender=Subscription)

    @responses.activate
    def test_scheduled_metrics(self):
        # Setup
        # deactivate Testsession for this test
        self.session = None
        # add metric post response
        responses.add(responses.POST,
                      "http://metrics-url/metrics/",
                      json={"foo": "bar"},
                      status=200, content_type='application/json')

        # Execute
        result = scheduled_metrics.apply_async()
        # Check
        self.assertEqual(result.get(), "5 Scheduled metrics launched")
        # fire_messagesets_tasks fires two metrics, therefore extra call
        self.assertEqual(len(responses.calls), 6)

    def test_fire_active_last(self):
        # Setup
        adapter = self._mount_session()
        # make two active and one inactive subscription
        self.make_subscription()
        self.make_subscription()
        sub = self.make_subscription()
        sub.active = False
        sub.completed = True
        sub.save()

        # Execute
        result = tasks.fire_active_last.apply_async()

        # Check
        self.assertEqual(
            result.get().get(),
            "Fired metric <subscriptions.active.last> with value <2.0>"
        )
        self.check_request(
            adapter.request, 'POST',
            data={"subscriptions.active.last": 2.0}
        )

    def test_fire_created_last(self):
        # Setup
        adapter = self._mount_session()
        # make two active and one inactive subscription
        self.make_subscription()
        self.make_subscription()
        sub = self.make_subscription()
        sub.active = False
        sub.completed = True
        sub.save()

        # Execute
        result = tasks.fire_created_last.apply_async()

        # Check
        self.assertEqual(
            result.get().get(),
            "Fired metric <subscriptions.created.last> with value <3.0>"
        )
        self.check_request(
            adapter.request, 'POST',
            data={"subscriptions.created.last": 3.0}
        )

    def test_fire_broken_last(self):
        # Setup
        adapter = self._mount_session()
        # make two healthy subscriptions
        self.make_subscription()
        sub = self.make_subscription()
        sub.process_status = 1
        sub.save()
        # make two broken subscriptions
        sub = self.make_subscription()
        sub.process_status = -1
        sub.save()
        sub = self.make_subscription()
        sub.messageset = self.messageset_audio
        sub.process_status = -1
        sub.save()

        # Execute
        result = tasks.fire_broken_last.apply_async()

        # Check
        self.assertEqual(
            result.get().get(),
            "Fired metric <subscriptions.broken.last> with value <2.0>"
        )
        self.check_request(
            adapter.request, 'POST',
            data={"subscriptions.broken.last": 2.0}
        )

    def test_fire_completed_last(self):
        # Setup
        adapter = self._mount_session()
        # make two incomplete and one complete subscription
        self.make_subscription()
        self.make_subscription()
        sub = self.make_subscription()
        sub.completed = True
        sub.save()

        # Execute
        result = tasks.fire_completed_last.apply_async()

        # Check
        self.assertEqual(
            result.get().get(),
            "Fired metric <subscriptions.completed.last> with value <1.0>"
        )
        self.check_request(
            adapter.request, 'POST',
            data={"subscriptions.completed.last": 1.0}
        )

    def test_messagesets_tasks(self):
        # Setup
        self._mount_session()
        self.make_subscription()

        # Execute
        result = tasks.fire_messagesets_tasks.apply_async()

        # Check
        self.assertEqual(
            result.get(),
            "2 MessageSet metrics launched"
        )

    def test_mesageset_last(self):
        # Setup
        adapter = self._mount_session()
        self.make_subscription()

        # Execute
        result = tasks.fire_messageset_last.apply_async(kwargs={
            "msgset_id": self.messageset.id,
            "short_name": self.messageset.short_name
        })

        # Check
        self.assertEqual(
            result.get().get(),
            "Fired metric <subscriptions.messageset_one.active.last> with "
            "value <1.0>"
        )
        self.check_request(
            adapter.request, 'POST',
            data={"subscriptions.messageset_one.active.last": 1.0}
        )


class TestUserCreation(AuthenticatedAPITestCase):

    def test_create_user_and_token(self):
        # Setup
        user_request = {"email": "test@example.org"}
        # Execute
        request = self.adminclient.post('/api/v1/user/token/', user_request)
        token = request.json().get('token', None)
        # Check
        self.assertIsNotNone(
            token, "Could not receive authentication token on post.")
        self.assertEqual(
            request.status_code, 201,
            "Status code on /api/v1/user/token/ was %s (should be 201)."
            % request.status_code)

    def test_create_user_and_token_fail_nonadmin(self):
        # Setup
        user_request = {"email": "test@example.org"}
        # Execute
        request = self.client.post('/api/v1/user/token/', user_request)
        error = request.json().get('detail', None)
        # Check
        self.assertIsNotNone(
            error, "Could not receive error on post.")
        self.assertEqual(
            error, "You do not have permission to perform this action.",
            "Error message was unexpected: %s."
            % error)

    def test_create_user_and_token_not_created(self):
        # Setup
        user_request = {"email": "test@example.org"}
        # Execute
        request = self.adminclient.post('/api/v1/user/token/', user_request)
        token = request.json().get('token', None)
        # And again, to get the same token
        request2 = self.adminclient.post('/api/v1/user/token/', user_request)
        token2 = request2.json().get('token', None)

        # Check
        self.assertEqual(
            token, token2,
            "Tokens are not equal, should be the same as not recreated.")

    def test_create_user_new_token_nonadmin(self):
        # Setup
        user_request = {"email": "test@example.org"}
        request = self.adminclient.post('/api/v1/user/token/', user_request)
        token = request.json().get('token', None)
        cleanclient = APIClient()
        cleanclient.credentials(HTTP_AUTHORIZATION='Token %s' % token)
        # Execute
        request = cleanclient.post('/api/v1/user/token/', user_request)
        error = request.json().get('detail', None)
        # Check
        # new user should not be admin
        self.assertIsNotNone(
            error, "Could not receive error on post.")
        self.assertEqual(
            error, "You do not have permission to perform this action.",
            "Error message was unexpected: %s."
            % error)


class TestHealthcheckAPI(AuthenticatedAPITestCase):

    def test_healthcheck_read(self):
        # Setup
        # Execute
        response = self.client.get('/api/health/',
                                   content_type='application/json')
        # Check
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["up"], True)
        self.assertEqual(response.data["result"]["database"], "Accessible")

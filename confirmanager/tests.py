# coding: utf-8
import datetime
from django.core.urlresolvers import reverse
from django.test.utils import override_settings

from mock import patch
from django.test import TestCase
import mock

from .utils import mock_signal_receiver
from .models import EmailConfirmation, ConfirmationExpired
from .signals import email_confirmed
from .factories import ConfirmationFactory, UserFactory


@patch('confirmanager.models.now')
@override_settings(CONFIRMANAGER_EXPIRES=3)
class TestModel(TestCase):

    def test_is_key_expired(self, mock_now):
        mock_now.return_value = datetime.datetime(2015, 10, 21)  # Back to the future 2
        expired = ConfirmationFactory.build(sent_on=datetime.datetime(2015, 10, 18))
        self.assertTrue(expired.is_key_expired)

    def test_is_key_not_expired(self, mock_now):
        mock_now.return_value = datetime.datetime(1985, 11, 5)  # Back to the future 3
        not_expired = ConfirmationFactory.build(sent_on=datetime.datetime(1985, 11, 3))
        self.assertFalse(not_expired.is_key_expired)


@override_settings(CONFIRMANAGER_EXPIRES=3)
class TestManager(TestCase):

    @patch('confirmanager.models.now')
    def test_delete_expired_confirmations(self, mock_now):
        mock_now.return_value = datetime.datetime(2015, 10, 21)  # Back to the future 2
        expired = ConfirmationFactory(sent_on=datetime.datetime(1980, 1, 1), email='foo@bar.com')
        not_expired = ConfirmationFactory(sent_on=datetime.datetime(2020, 1, 1), email='baz@bar.com')
        EmailConfirmation.objects.delete_expired_confirmations()
        self.assertQuerysetEqual(EmailConfirmation.objects.all(), ['<EmailConfirmation: for baz@bar.com>'])


class TestDoConfirm(TestCase):

    def setUp(self):
        self.email_address = 'foo@bar.com'
        self.confirmation = ConfirmationFactory(email=self.email_address)

    def assertIsConfirmed(self, result, confirmation):
        self.assertEqual(result, confirmation)
        confirmation = EmailConfirmation.objects.get(email=confirmation.email)
        self.assertEqual(confirmation.user.email, self.confirmation.email)
        self.assertTrue(confirmation.is_verified)

    def assertIsNotConfirmed(self, result, confirmation):
        self.assertEqual(result, None)
        confirmation = EmailConfirmation.objects.get(email=confirmation.email)
        self.assertNotEqual(confirmation.user.email, self.confirmation.email)
        self.assertFalse(confirmation.is_verified)

    def test_confirm_existing_email_confirmation(self):
        with mock_signal_receiver(email_confirmed) as receiver_mock:
            result = EmailConfirmation.objects.confirm(self.confirmation.confirmation_key)
            self.assertIsConfirmed(result, self.confirmation)
            receiver_mock.assert_called_once_with(signal=mock.ANY, email=self.email_address, sender=mock.ANY)

    def test_confirm_email_not_exists(self):
        with mock_signal_receiver(email_confirmed) as receiver_mock:
            self.assertFalse(self.confirmation.is_verified)
            result = EmailConfirmation.objects.confirm('xxx')
            self.assertIsNotConfirmed(result, self.confirmation)
            self.assertEqual(receiver_mock.call_count, 0)

    def test_confirm_expired_token(self):
        with mock_signal_receiver(email_confirmed) as receiver_mock:
            self.confirmation.sent_on = datetime.datetime(1985, 11, 5)  # expire
            self.confirmation.save()
            self.assertRaises(ConfirmationExpired, EmailConfirmation.objects.confirm, self.confirmation.confirmation_key)
            self.assertEqual(receiver_mock.call_count, 0)


class TestLastUnconfirmed(TestCase):

    def setUp(self):
        self.user = UserFactory(email='foo@bar.com')

    def test_two_unconfirmed(self):
        confirm1 = ConfirmationFactory(user=self.user, email='alice@evil.com')
        confirm2 = ConfirmationFactory(user=self.user, email='mallory@evil.com')
        latest_unconfirmed = EmailConfirmation.objects.last_email_for(self.user)
        self.assertEqual(latest_unconfirmed, ('mallory@evil.com', False))

    def test_no_unconfirmed(self):
        latest_unconfirmed = EmailConfirmation.objects.last_email_for(self.user)
        self.assertEqual(latest_unconfirmed, ('foo@bar.com', True))


class TestSend(TestCase):

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                       DEFAULT_FROM_EMAIL='hey@bulldog.com')
    def test_send(self):
        user = UserFactory()
        email = 'foo@bar.baz'
        confirmation = EmailConfirmation.objects.send_confirmation(email, user=user)

        from django.core import mail
        self.assertEqual(confirmation.email, email)
        self.assertTrue(confirmation.confirmation_key in mail.outbox[0].body)
        self.assertEqual('hey@bulldog.com', mail.outbox[0].from_email)


@override_settings(CONFIRMANAGER_REDIRECT_URL='/REDIRECT_URL/',
                   CONFIRMANAGER_LOGIN_URL='/LOGIN_URL/',)
class TestViewExpired(TestCase):

    def setUp(self):
        self.confirmation = ConfirmationFactory(email='hello@bar.com', user__email='foo@bar.com', is_expired=True)

    def test_handle_expired_authenticated(self):
        self.assertTrue(self.client.login(username=self.confirmation.user.username, password='1234'))
        response = self.client.get(reverse('confirmation-view', args=[self.confirmation.confirmation_key]))
        self.assertRedirects(response, '/REDIRECT_URL/')
        self.assertQuerysetEqual(EmailConfirmation.objects.all(), ['<EmailConfirmation: for hello@bar.com>'])
        self.assertFalse(EmailConfirmation.objects.get(email='hello@bar.com').is_key_expired)

    def test_handle_expired_anonymous(self):
        self.client.logout()
        response = self.client.get(reverse('confirmation-view', args=[self.confirmation.confirmation_key]))
        self.assertRedirects(response, '/LOGIN_URL/?email=foo@bar.com&next=/REDIRECT_URL/')
        self.assertQuerysetEqual(EmailConfirmation.objects.all(), ['<EmailConfirmation: for hello@bar.com>'])
        self.assertFalse(EmailConfirmation.objects.get(email='hello@bar.com').is_key_expired)


@override_settings(CONFIRMANAGER_REDIRECT_URL='/REDIRECT_URL/',
                   CONFIRMANAGER_LOGIN_URL='/LOGIN_URL/',)
class TestViewMissing(TestCase):

    def test_handle_missing_authenticated(self):
        self.assertTrue(self.client.login(username=UserFactory().username, password='1234'))
        response = self.client.get(reverse('confirmation-view', args=['xxx']))
        self.assertRedirects(response, '/REDIRECT_URL/')

    def test_handle_missing_anonymous(self):
        self.client.logout()
        response = self.client.get(reverse('confirmation-view', args=['xxx']))
        self.assertRedirects(response, '/LOGIN_URL/?next=/REDIRECT_URL/')


@override_settings(CONFIRMANAGER_REDIRECT_URL='/REDIRECT_URL/',
                   CONFIRMANAGER_LOGIN_URL='/LOGIN_URL/',)
class TestViewOk(TestCase):

    def setUp(self):
        self.confirmation = ConfirmationFactory(email='hello@bar.com', user__email='foo@bar.com')

    def test_handle_ok_authenticated(self):
        self.assertTrue(self.client.login(username=self.confirmation.user.username, password='1234'))
        response = self.client.get(reverse('confirmation-view', args=[self.confirmation.confirmation_key]))
        self.assertRedirects(response, '/REDIRECT_URL/')
        self.assertTrue(EmailConfirmation.objects.get(pk=self.confirmation.pk).is_verified)

    def test_handle_ok_anonymous(self):
        self.client.logout()
        response = self.client.get(reverse('confirmation-view', args=[self.confirmation.confirmation_key]))
        self.assertRedirects(response, '/LOGIN_URL/?email=hello@bar.com&next=/REDIRECT_URL/')
        self.assertTrue(EmailConfirmation.objects.get(pk=self.confirmation.pk).is_verified)

    def test_handle_ok_another_user(self):
        self.assertTrue(self.client.login(username=self.confirmation.user.username, password='1234'))
        response = self.client.get(reverse('confirmation-view', args=[self.confirmation.confirmation_key]))
        self.assertRedirects(response, '/REDIRECT_URL/')
        self.assertTrue(EmailConfirmation.objects.get(pk=self.confirmation.pk).is_verified)
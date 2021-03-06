from django.test import TestCase
from django.utils import timezone
from django.contrib.auth.models import User
from django.shortcuts import reverse
from django.http import Http404
from django.contrib.sites.models import Site

import datetime
from unittest import mock

from allauth.socialaccount.models import SocialApp, SocialAccount, SocialToken

from twitterscheduler.models import Tweet, ScheduledTweet, Profile
from twitterscheduler.tasks import get_authed_tweepy, sync_tweets_task, tweet_task


class DictToObj:
    def __init__(self, **kwargs):
        for key, val in kwargs.items():
            setattr(self, key, val)


class TestTwitterIntegrations(TestCase):

    @classmethod
    def setUpTestData(cls):
        Site.objects.create(domain='http://127.0.0.1:8000', name='localhost')

    def setUp(self):
        site = Site.objects.get(name='localhost')
        self.app = SocialApp.objects.create(provider='twitter', name='twitter', client_id='id_1234',
                                            secret='secret_1234')
        self.app.sites.add(site)
        self.app.save()
        self.user = User.objects.create_user('bob', password='nice_pass')
        self.mock_tweets = [
            DictToObj(id_str=str(i), text=f'text {i}', created_at=timezone.now() + datetime.timedelta(minutes=-i-15))
            for i in range(10)
        ]

    def test_get_authed_tweepy_returns_404_when_no_twitter_app(self):
        SocialApp.objects.get(name='twitter').delete()
        self.assertRaises(Http404, get_authed_tweepy, '1', '1')

    def test_get_authed_tweepy_returns_instance_with_credentials_set(self):
        authed = get_authed_tweepy('123', '456')
        self.assertEqual(authed.auth.consumer_key, b'id_1234')
        self.assertEqual(authed.auth.consumer_secret, b'secret_1234')
        self.assertEqual(authed.auth.access_token, '123')
        self.assertEqual(authed.auth.access_token_secret, '456')


class TestSyncTweetsTask(TestCase):

    @classmethod
    def setUpTestData(cls):
        Site.objects.create(domain='http://127.0.0.1:8000', name='localhost')

    def setUp(self):
        site = Site.objects.get(name='localhost')
        self.app = SocialApp.objects.create(provider='twitter', name='twitter', client_id='id_1234',
                                            secret='secret_1234')
        self.app.sites.add(site)
        self.app.save()
        self.user = User.objects.create_user('bob', password='nice_pass')
        self.social_acc = SocialAccount.objects.create(user=self.user, provider='twitter')
        self.social_tokens = SocialToken.objects.create(account=self.social_acc, app=self.app, token='1', token_secret='2')
        self.mock_tweets = [
            DictToObj(id_str=str(i), text=f'text {i}', created_at=timezone.now() + datetime.timedelta(minutes=-i-15))
            for i in range(10)
        ]

    @mock.patch('twitterscheduler.tasks.get_authed_tweepy')
    def test_sync_tweets_task_no_tweets_created_when_no_tweets_on_twitter(self, mock_tweepy):
        mock_tweepy.return_value.user_timeline = lambda: []

        sync_tweets_task(self.user.username)
        tweets = Tweet.objects.filter(user=self.user)
        self.assertEqual(len(tweets), 0)
        tweets = Tweet.objects.all()
        self.assertEqual(len(tweets), 0)

    @mock.patch('twitterscheduler.tasks.get_authed_tweepy')
    def test_sync_tweets_task_tweets_added_when_db_is_empty(self, mock_tweepy):
        mock_tweepy.return_value.user_timeline = lambda: self.mock_tweets[:5]

        sync_tweets_task(self.user.username)
        tweets = Tweet.objects.filter(user=self.user)
        self.assertEqual(len(tweets), 5)

    @mock.patch('twitterscheduler.tasks.get_authed_tweepy')
    def test_sync_tweets_task_tweets_not_added_when_existing_in_db(self, mock_tweepy):
        mock_tweepy.return_value.user_timeline = lambda: self.mock_tweets[:5]

        for mock_tweet in self.mock_tweets[:5]:
            Tweet.objects.create(tweet_id=mock_tweet.id_str, user=self.user, text='boogala')

        sync_tweets_task(self.user.username)
        tweets = Tweet.objects.filter(user=self.user)
        self.assertEqual(len(tweets), 5)
        for tweet in tweets:
            self.assertEqual(tweet.text, 'boogala')

    @mock.patch('twitterscheduler.tasks.get_authed_tweepy')
    def test_sync_tweets_task_only_new_tweets_added(self, mock_tweepy):
        # new as in not already in db. doesn't have to do with time.
        mock_tweepy.return_value.user_timeline = lambda: self.mock_tweets[:3]

        for mock_tweet in self.mock_tweets[:1]:
            Tweet.objects.create(tweet_id=mock_tweet.id_str, user=self.user, text='boogala')

        tweets = Tweet.objects.filter(user=self.user)
        self.assertEqual(len(tweets), 1)

        sync_tweets_task(self.user.username)
        tweets = Tweet.objects.filter(user=self.user)
        self.assertEqual(len(tweets), 3)
        self.assertEqual(tweets[0].text, 'boogala')

    @mock.patch('twitterscheduler.tasks.get_authed_tweepy')
    def test_sync_tweets_task_dont_add_tweets_within_last_5_minutes(self, mock_tweepy):
        new_tweet = DictToObj(id_str='20', text=f'text 20', created_at=timezone.now() + datetime.timedelta(minutes=-1))
        new_tweet2 = DictToObj(id_str='21', text=f'text 21', created_at=timezone.now() + datetime.timedelta(minutes=-4, seconds=59))
        mock_tweepy.return_value.user_timeline = lambda: [new_tweet, new_tweet2]

        sync_tweets_task(self.user.username)
        tweets = Tweet.objects.filter(user=self.user)
        self.assertEqual(len(tweets), 0)

    @mock.patch('twitterscheduler.tasks.get_authed_tweepy')
    def test_profile_recently_synced_after_syncing(self, mock_tweepy):
        mock_tweepy.return_value.user_timeline = lambda: self.mock_tweets

        sync_tweets_task(self.user.username)
        profile = Profile.objects.get(user=self.user)
        self.assertIs(profile.synced_tweets_recently(), True)


class TestTweetTask(TestCase):
    @classmethod
    def setUpTestData(cls):
        Site.objects.create(domain='http://127.0.0.1:8000', name='localhost')

    def setUp(self):
        site = Site.objects.get(name='localhost')
        self.app = SocialApp.objects.create(provider='twitter', name='twitter', client_id='id_1234',
                                            secret='secret_1234')
        self.app.sites.add(site)
        self.app.save()
        self.user = User.objects.create_user('bob', password='nice_pass')
        self.tweet = Tweet.objects.create(user=self.user, text='nice tweet')
        self.social_acc = SocialAccount.objects.create(user=self.user, provider='twitter')
        self.social_tokens = SocialToken.objects.create(account=self.social_acc, app=self.app, token='1', token_secret='2')

    @mock.patch('twitterscheduler.tasks.get_authed_tweepy')
    def test_send_tweet_with_correct_args(self, mock_tweepy):
        mock_tweepy.return_value.update_status = mock.Mock(autospec=True)
        time_to_tweet = timezone.now() + datetime.timedelta(minutes=2)
        mock_tweepy.return_value.update_status.return_value = DictToObj(id_str='1234', time_posted_at=time_to_tweet)
        scheduled_tweet = ScheduledTweet.objects.create(tweet=self.tweet, time_to_tweet=time_to_tweet)
        tweet_task('bob', scheduled_tweet.id)
        mock_tweepy.return_value.update_status.assert_called_with(status='nice tweet')

    @mock.patch('twitterscheduler.tasks.get_authed_tweepy')
    def test_update_tweet_after_sending(self, mock_tweepy):
        mock_tweepy.return_value.update_status = mock.Mock(autospec=True)
        time_to_tweet = timezone.now() + datetime.timedelta(minutes=2)
        mock_tweepy.return_value.update_status.return_value = DictToObj(id_str='1234', time_posted_at=time_to_tweet)
        scheduled_tweet = ScheduledTweet.objects.create(tweet=self.tweet, time_to_tweet=time_to_tweet)
        tweet_task('bob', scheduled_tweet.id)
        tweet = Tweet.objects.get(pk=scheduled_tweet.tweet.id)
        self.assertEqual(tweet.tweet_id, '1234')
        self.assertIsNotNone(tweet.time_posted_at, time_to_tweet)
        self.assertIs(tweet.is_posted, True)

    @mock.patch('twitterscheduler.tasks.get_authed_tweepy')
    def test_scheduled_tweet_is_deleted_after_sending_tweet(self, mock_tweepy):
        mock_tweepy.return_value.update_status = mock.Mock(autospec=True)
        time_to_tweet = timezone.now() + datetime.timedelta(minutes=2)
        mock_tweepy.return_value.update_status.return_value = DictToObj(id_str='1234', time_posted_at=time_to_tweet)
        scheduled_tweet = ScheduledTweet.objects.create(tweet=self.tweet, time_to_tweet=time_to_tweet)
        tweet_task('bob', scheduled_tweet.id)
        self.assertEqual(len(ScheduledTweet.objects.filter(pk=scheduled_tweet.id)), 0)

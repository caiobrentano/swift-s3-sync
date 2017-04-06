import json
import mock
from s3_sync.sync_container import SyncContainer
import unittest


class TestSyncContainer(unittest.TestCase):
    class MockMetaConf(object):
        def __init__(self, fake_status):
            self.fake_status = fake_status
            self.write_buf = ''

        def read(self, size=-1):
            if size != -1:
                raise RuntimeError()
            return json.dumps(self.fake_status)

        def write(self, data):
            # Only support write at the beginning
            self.write_buf += data

        def truncate(self, size=None):
            if size:
                raise RuntimeError('Not supported')
            self.fake_status = json.loads(self.write_buf)
            self.write_buf = ''

        def __exit__(self, *args):
            if self.write_buf:
                self.fake_status = json.loads(self.write_buf)
                self.write_buf = ''

        def __enter__(self):
            return self

        def seek(self, offset, flags=None):
            if offset != 0:
                raise RuntimeError

    @mock.patch('s3_sync.sync_s3.boto3.session.Session')
    @mock.patch(
        's3_sync.sync_container.container_crawler.base_sync.InternalClient')
    def setUp(self, mock_ic, mock_boto3):
        self.mock_ic = mock.Mock()
        self.mock_boto3_session = mock.Mock()
        self.mock_boto3_client = mock.Mock()

        mock_ic.return_value = self.mock_ic
        mock_boto3.return_value = self.mock_boto3_session
        self.mock_boto3_session.client.return_value = self.mock_boto3_client

        self.aws_bucket = 'bucket'
        self.scratch_space = 'scratch'
        self.sync_container = SyncContainer(self.scratch_space,
                                            {'aws_bucket': self.aws_bucket,
                                             'aws_identity': 'identity',
                                             'aws_secret': 'credential',
                                             'account': 'account',
                                             'container': 'container'})

    def test_load_non_existent_meta(self):
        ret = self.sync_container.get_last_row('db-id')
        self.assertEqual(0, ret)

    @mock.patch('__builtin__.open')
    @mock.patch('s3_sync.sync_container.os.path.exists')
    def test_load_upgrade_status(self, mock_exists, mock_open):
        mock_exists.return_value = True
        fake_status = dict(last_row=42)
        mock_open.return_value = self.MockMetaConf(fake_status)

        status = self.sync_container.get_last_row('db-id')
        self.assertEqual(fake_status['last_row'], status)

        mock_exists.assert_called_with('%s/%s/%s' % (
            self.scratch_space, self.sync_container._account,
            self.sync_container._container))

    @mock.patch('__builtin__.open')
    @mock.patch('s3_sync.sync_container.os.path.exists')
    def test_last_row_new_bucket(self, mock_exists, mock_open):
        db_id = 'db-id-test'
        new_bucket = 'new-bucket'
        self.sync_container.aws_bucket = 'bucket'
        fake_status = {db_id: dict(last_row=42, aws_bucket=new_bucket)}

        mock_exists.return_value = True
        mock_open.return_value = self.MockMetaConf(fake_status)

        status = self.sync_container.get_last_row(db_id)
        self.assertEqual(0, status)

        mock_exists.assert_called_with('%s/%s/%s' % (
            self.scratch_space, self.sync_container._account,
            self.sync_container._container))

    @mock.patch('__builtin__.open')
    @mock.patch('s3_sync.sync_container.os.path.exists')
    def test_last_row_new_db_id(self, mock_exists, mock_open):
        db_id = 'db-id-test'
        self.sync_container.aws_bucket = 'bucket'
        fake_status = {db_id: dict(last_row=42, aws_bucket='bucket')}

        mock_exists.return_value = True
        mock_open.return_value = self.MockMetaConf(fake_status)

        status = self.sync_container.get_last_row('other-db-id')
        self.assertEqual(0, status)

        mock_exists.assert_called_with('%s/%s/%s' % (
            self.scratch_space, self.sync_container._account,
            self.sync_container._container))

    @mock.patch('__builtin__.open')
    @mock.patch('s3_sync.sync_container.os.path.exists')
    def test_last_row(self, mock_exists, mock_open):
        db_entries = [{'id': 'db-id-1', 'aws_bucket': 'bucket', 'last_row': 5},
                      {'id': 'db-id-2', 'aws_bucket': 'bucket', 'last_row': 7}]
        for entry in db_entries:
            self.sync_container.aws_bucket = entry['aws_bucket']
            fake_status = {entry['id']: dict(last_row=entry['last_row'],
                                             aws_bucket=entry['aws_bucket'])}

            mock_exists.return_value = True
            mock_open.return_value = self.MockMetaConf(fake_status)

            status = self.sync_container.get_last_row(entry['id'])
            self.assertEqual(entry['last_row'], status)

            mock_exists.assert_called_with('%s/%s/%s' % (
                self.scratch_space, self.sync_container._account,
                self.sync_container._container))

    @mock.patch('__builtin__.open')
    def test_save_last_row(self, mock_open):
        db_entries = {'db-id-1': {'aws_bucket': 'bucket', 'last_row': 5},
                      'db-id-2': {'aws_bucket': 'bucket', 'last_row': 7}}
        new_row = 42
        for db_id, entry in db_entries.items():
            self.sync_container.aws_bucket = entry['aws_bucket']
            fake_conf_file = self.MockMetaConf(db_entries)
            mock_open.return_value = fake_conf_file

            with mock.patch('s3_sync.sync_container.os.path.exists')\
                    as mock_exists:
                mock_exists.return_value = True

                self.sync_container.save_last_row(new_row, db_id)
                file_entries = fake_conf_file.fake_status
                for file_db_id, status in file_entries.items():
                    if file_db_id == db_id:
                        self.assertEqual(new_row, status['last_row'])
                    else:
                        self.assertEqual(db_entries[file_db_id]['last_row'],
                                         status['last_row'])

                self.assertEqual(
                    [mock.call('%s/%s' % (self.scratch_space,
                                          self.sync_container._account)),
                     mock.call('%s/%s/%s' % (self.scratch_space,
                                             self.sync_container._account,
                                             self.sync_container._container))],
                    mock_exists.call_args_list)

    @mock.patch('__builtin__.open')
    @mock.patch('s3_sync.sync_container.os.path.exists')
    def test_save_no_prior_status(self, mock_exists, mock_open):
        def existence_check(path):
            if path == '%s/%s' % (self.scratch_space,
                                  self.sync_container._account):
                return True
            elif path == '%s/%s/%s' % (self.scratch_space,
                                       self.sync_container._account,
                                       self.sync_container._container):
                return False
            else:
                raise RuntimeError('Invalid path')

        self.sync_container.aws_bucket = 'bucket'
        fake_conf_file = self.MockMetaConf({})
        mock_exists.side_effect = existence_check
        mock_open.return_value = fake_conf_file

        self.sync_container.save_last_row(42, 'db-id')
        self.assertEqual(42, fake_conf_file.fake_status['db-id']['last_row'])
        self.assertEqual('bucket',
                         fake_conf_file.fake_status['db-id']['aws_bucket'])

        self.assertEqual(
            [mock.call('%s/%s' % (self.scratch_space,
                                  self.sync_container._account)),
             mock.call('%s/%s/%s' % (self.scratch_space,
                                     self.sync_container._account,
                                     self.sync_container._container))],
            mock_exists.call_args_list)

    @mock.patch('__builtin__.open')
    @mock.patch('s3_sync.sync_container.os.path.exists')
    def test_save_last_row_new_bucket(self, mock_exists, mock_open):
        db_entries = {'db-id-1': {'aws_bucket': 'bucket', 'last_row': 5},
                      'db-id-2': {'aws_bucket': 'old-bucket', 'last_row': 7}}
        new_row = 42
        for db_id, entry in db_entries.items():
            self.sync_container.aws_bucket = 'bucket'
            fake_conf_file = self.MockMetaConf(db_entries)
            mock_open.return_value = fake_conf_file

            with mock.patch('s3_sync.sync_container.os.path.exists')\
                    as mock_exists:
                mock_exists.return_value = True
                self.sync_container.save_last_row(new_row, db_id)
                file_entries = fake_conf_file.fake_status
                for file_db_id, status in file_entries.items():
                    if file_db_id == db_id:
                        self.assertEqual(new_row, status['last_row'])
                        self.assertEqual('bucket', status['aws_bucket'])
                    else:
                        self.assertEqual(db_entries[file_db_id]['last_row'],
                                         status['last_row'])
                        self.assertEqual(db_entries[file_db_id]['aws_bucket'],
                                         status['aws_bucket'])

                self.assertEqual(
                    [mock.call('%s/%s' % (self.scratch_space,
                                          self.sync_container._account)),
                     mock.call('%s/%s/%s' % (self.scratch_space,
                                             self.sync_container._account,
                                             self.sync_container._container))],
                    mock_exists.call_args_list)

    @mock.patch(
        's3_sync.sync_container.container_crawler.base_sync.InternalClient')
    def test_s3_provider(self, mock_ic):
        ic = mock.Mock()
        mock_ic.return_value = ic

        defaults = {'aws_bucket': self.aws_bucket,
                    'aws_identity': 'identity',
                    'aws_secret': 'credential',
                    'account': 'account',
                    'container': 'container'}
        test_settings = [defaults,
                         dict(defaults.items() + [('provider', 's3')])]

        for settings in test_settings:
            with mock.patch('s3_sync.sync_container.SyncS3') as mock_sync_s3:
                SyncContainer(self.scratch_space, settings, max_conns=1)
                mock_sync_s3.assert_called_once_with(ic, settings, 1)

    @mock.patch(
        's3_sync.sync_container.container_crawler.base_sync.InternalClient')
    def test_swift_provider(self, mock_ic):
        ic = mock.Mock()
        mock_ic.return_value = ic

        settings = {'aws_bucket': self.aws_bucket,
                    'aws_identity': 'identity',
                    'aws_secret': 'credential',
                    'account': 'account',
                    'container': 'container',
                    'provider': 'swift'}
        with mock.patch('s3_sync.sync_container.SyncSwift') as mock_sync_swift:
            SyncContainer(self.scratch_space, settings, max_conns=1)
            mock_sync_swift.assert_called_once_with(ic, settings, 1)

    @mock.patch(
        's3_sync.sync_container.container_crawler.base_sync.InternalClient')
    def test_unknown_provider(self, mock_ic):
        ic = mock.Mock()
        mock_ic.return_value = ic

        settings = {'aws_bucket': self.aws_bucket,
                    'aws_identity': 'identity',
                    'aws_secret': 'credential',
                    'account': 'account',
                    'container': 'container',
                    'provider': 'foo'}
        with self.assertRaises(NotImplementedError):
            SyncContainer(self.scratch_space, settings, 1)

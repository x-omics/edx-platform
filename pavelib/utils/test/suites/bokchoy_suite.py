"""
Class used for defining and running Bok Choy acceptance test suite
"""
from paver.easy import sh
from pavelib.utils.test.suites import TestSuite
from pavelib.utils.envs import Env
from pavelib.utils.test import bokchoy_utils
from pavelib.utils.test import utils as test_utils
from pavelib.utils.process import kill_process

try:
    from pygments.console import colorize
except ImportError:
    colorize = lambda color, text: text  # pylint: disable-msg=invalid-name

__test__ = False  # do not collect


class BokChoyTestSuite(TestSuite):
    """
    TestSuite for running Bok Choy tests
    """
    def __init__(self, *args, **kwargs):
        super(BokChoyTestSuite, self).__init__(*args, **kwargs)
        self.test_dir = Env.BOK_CHOY_DIR / "tests"
        self.log_dir = Env.BOK_CHOY_LOG_DIR
        self.report_dir = Env.BOK_CHOY_REPORT_DIR
        self.xunit_report = self.report_dir / "xunit.xml"
        self.cache = Env.BOK_CHOY_CACHE
        self.fasttest = kwargs.get('fasttest', False)
        self.test_spec = kwargs.get('test_spec', None)
        self.verbosity = kwargs.get('verbosity', 2)
        self.server_pids = []

    def __enter__(self):
        super(BokChoyTestSuite, self).__enter__()

        # Ensure that we have a directory to put logs and reports
        self.log_dir.makedirs_p()
        self.report_dir.makedirs_p()
        test_utils.clean_reports_dir()

        msg = colorize('green', "Checking for mongo, memchache, and mysql...")
        print(msg)
        bokchoy_utils.check_services()

        if not self.fasttest:
            # Process assets and set up database for bok-choy tests
            # Reset the database
            sh("{}/scripts/reset-test-db.sh".format(Env.REPO_ROOT))

            # Collect static assets
            sh("paver update_assets --settings=bok_choy")

        # Clear any test data already in Mongo or MySQLand invalidate
        # the cache
        bokchoy_utils.clear_mongo()
        self.cache.flush_all()

        sh(
            "./manage.py lms --settings bok_choy loaddata --traceback"
            " common/test/db_fixtures/*.json"
        )

        # Ensure the test servers are available
        msg = colorize('green', "Starting test servers...")
        print(msg)
        self.server_pids = bokchoy_utils.start_servers()

        msg = colorize('green', "Waiting for servers to start...")
        print(msg)
        bokchoy_utils.wait_for_test_servers()

    def __exit__(self, exc_type, exc_value, traceback):
        super(BokChoyTestSuite, self).__exit__(exc_type, exc_value, traceback)

        msg = colorize('green', "Cleaning up databases...")
        print(msg)

        for pid in self.server_pids:
            kill_process(pid)

        # Clean up data we created in the databases
        sh("./manage.py lms --settings bok_choy flush --traceback --noinput")
        bokchoy_utils.clear_mongo()

    @property
    def cmd(self):
        # Default to running all tests if no specific test is specified
        if not self.test_spec:
            test_spec = self.test_dir
        else:
            test_spec = self.test_dir / self.test_spec

        # Construct the nosetests command, specifying where to save
        # screenshots and XUnit XML reports
        cmd = [
            "SCREENSHOT_DIR='{}'".format(self.log_dir),
            "nosetests",
            test_spec,
            "--with-xunit",
            "--with-flaky",
            "--xunit-file={}".format(self.xunit_report),
            "--verbosity={}".format(self.verbosity),
        ]

        cmd = (" ").join(cmd)
        return cmd

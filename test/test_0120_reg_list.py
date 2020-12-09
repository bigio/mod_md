# test mod_md acme terms-of-service handling

import os

from shutil import copyfile
from TestEnv import TestEnv


def setup_module(module):
    print("setup_module: %s" % module.__name__)
    TestEnv.init()


def teardown_module(module):
    print("teardown_module: %s" % module.__name__)


class TestRegAdd:

    def setup_method(self, method):
        print("setup_method: %s" % method.__name__)
        TestEnv.clear_store()

    def teardown_method(self, method):
        print("teardown_method: %s" % method.__name__)

    # test case: list empty store
    def test_120_000(self):
        assert TestEnv.a2md(["list"])['jout'] == TestEnv.EMPTY_JOUT

    # test case: list two managed domains
    def test_120_001(self):
        domains = [ 
            ["test120-001.com", "test120-001a.com", "test120-001b.com"],
            ["greenbytes2.de", "www.greenbytes2.de", "mail.greenbytes2.de"]
        ]
        for dns in domains:
            assert TestEnv.a2md(["add"] + dns)['rv'] == 0
        #
        # list all store content
        jout = TestEnv.a2md(["list"])['jout']
        assert len(jout['output']) == len(domains)
        domains.reverse()
        for i in range(0, len(jout['output'])):
            TestEnv.check_json_contains(jout['output'][i], {
                "name": domains[i][0],
                "domains": domains[i],
                "contacts": [],
                "ca": {
                    "url": TestEnv.ACME_URL,
                    "proto": "ACME"
                },
                "state": TestEnv.MD_S_INCOMPLETE
            })
        # list md by name
        for dns in ["test120-001.com", "greenbytes2.de"]:
            md = TestEnv.a2md(["list", dns])['jout']['output'][0]
            assert md['name'] == dns

    # test case: validate md state in store
    def test_120_002(self):
        # check: md without pkey/cert -> INCOMPLETE
        domain = "not-forbidden.org"
        assert TestEnv.a2md(["add", domain])['rv'] == 0
        assert TestEnv.a2md(["update", domain, "contacts", "admin@" + domain])['rv'] == 0
        assert TestEnv.a2md(["update", domain, "agreement", TestEnv.ACME_TOS])['rv'] == 0
        assert TestEnv.a2md(["list", domain])['jout']['output'][0]['state'] == TestEnv.MD_S_INCOMPLETE
        # check: valid pkey/cert -> COMPLETE
        copyfile(self._path_conf_ssl("valid_pkey.pem"), TestEnv.store_domain_file(domain, 'privkey.pem'))
        copyfile(self._path_conf_ssl("valid_cert.pem"), TestEnv.store_domain_file(domain, 'pubcert.pem'))
        assert TestEnv.a2md(["list", domain])['jout']['output'][0]['state'] == TestEnv.MD_S_COMPLETE
        # check: expired cert -> EXPIRED
        copyfile(self._path_conf_ssl("expired_pkey.pem"), TestEnv.store_domain_file(domain, 'privkey.pem'))
        copyfile(self._path_conf_ssl("expired_cert.pem"), TestEnv.store_domain_file(domain, 'pubcert.pem'))
        out = TestEnv.a2md(["list", domain])['jout']['output'][0]
        assert out['state'] == TestEnv.MD_S_INCOMPLETE
        assert out['renew'] is True

    # test case: broken cert file
    def test_120_003(self):
        domain = "not-forbidden.org"
        assert TestEnv.a2md(["add", domain])['rv'] == 0
        assert TestEnv.a2md(["update", domain, "contacts", "admin@" + domain])['rv'] == 0
        assert TestEnv.a2md(["update", domain, "agreement", TestEnv.ACME_TOS])['rv'] == 0
        # check: valid pkey/cert -> COMPLETE
        copyfile(self._path_conf_ssl("valid_pkey.pem"), TestEnv.store_domain_file(domain, 'privkey.pem'))
        copyfile(self._path_conf_ssl("valid_cert.pem"), TestEnv.store_domain_file(domain, 'pubcert.pem'))
        assert TestEnv.a2md(["list", domain])['jout']['output'][0]['state'] == TestEnv.MD_S_COMPLETE
        # check: replace cert by broken file -> ERROR
        copyfile(self._path_conf_ssl("valid_cert.req"), TestEnv.store_domain_file(domain, 'pubcert.pem'))
        assert TestEnv.a2md(["list", domain])['jout']['output'][0]['state'] == TestEnv.MD_S_ERROR

    # REMOVED: we no longer verify private keys at startup and leave that to the
    #          user of the key, e.g. mod_ssl. It is the ultimate arbiter of this.
    # def test_120_004(self):
    # test case: broken private key file

    def _path_conf_ssl(self, name):
        return os.path.join(TestEnv.APACHE_SSL_DIR, name) 

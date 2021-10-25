import copy
import inspect
import json
import logging

import pytest
import re
import os
import shutil
import subprocess
import time

from datetime import datetime, timedelta
from configparser import ConfigParser, ExtendedInterpolation
from typing import Dict, List, Optional

from pyhttpd.certs import CertificateSpec
from .md_cert_util import MDCertUtil
from pyhttpd.env import HttpdTestSetup, HttpdTestEnv
from pyhttpd.result import ExecResult

log = logging.getLogger(__name__)


class MDTestSetup(HttpdTestSetup):

    def __init__(self, env: 'HttpdTestEnv'):
        super().__init__(env=env)

    def make(self):
        super().make(add_modules=["proxy_connect", "md"])
        if "pebble" == self.env.acme_server:
            self._make_pebble_conf()

    def _make_pebble_conf(self):
        our_dir = os.path.dirname(inspect.getfile(MDTestSetup))
        conf_src_dir = os.path.join(our_dir, 'pebble')
        conf_dest_dir = os.path.join(self.env.gen_dir, 'pebble')
        if not os.path.exists(conf_dest_dir):
            os.makedirs(conf_dest_dir)
        for name in os.listdir(conf_src_dir):
            src_path = os.path.join(conf_src_dir, name)
            m = re.match(r'(.+).template', name)
            if m:
                self._make_template(src_path, os.path.join(conf_dest_dir, m.group(1)))
            elif os.path.isfile(src_path):
                shutil.copy(src_path, os.path.join(conf_dest_dir, name))


class MDTestEnv(HttpdTestEnv):

    MD_S_UNKNOWN = 0
    MD_S_INCOMPLETE = 1
    MD_S_COMPLETE = 2
    MD_S_EXPIRED = 3
    MD_S_ERROR = 4

    EMPTY_JOUT = {'status': 0, 'output': []}

    DOMAIN_SUFFIX = "%d.org" % time.time()
    LOG_FMT_TIGHT = '%(levelname)s: %(message)s'

    @classmethod
    def get_acme_server(cls):
        return os.environ['ACME'] if 'ACME' in os.environ else "pebble"

    @classmethod
    def has_acme_server(cls):
        return cls.get_acme_server() != 'none'

    @classmethod
    def has_acme_eab(cls):
        return cls.get_acme_server() == 'pebble'

    @classmethod
    def is_pebble(cls) -> bool:
        return cls.get_acme_server() == 'pebble'

    @classmethod
    def lacks_ocsp(cls):
        return cls.is_pebble()

    def __init__(self, pytestconfig=None, setup_dirs=True):
        super().__init__(pytestconfig=pytestconfig,
                         local_dir=os.path.dirname(inspect.getfile(MDTestEnv)),
                         add_base_conf="""
                            """,
                         interesting_modules=["md"])
        self._acme_server = self.get_acme_server()
        self._acme_tos = "accepted"
        self._acme_ca_pemfile = os.path.join(self.gen_dir, "apache/acme-ca.pem")
        if "pebble" == self._acme_server:
            self._acme_url = "https://localhost:14000/dir"
            self._acme_eab_url = "https://localhost:14001/dir"
        elif "boulder" == self._acme_server:
            self._acme_url = "http://localhost:4001/directory"
            self._acme_eab_url = None
        else:
            raise ExecResult(f"unknown ACME server type: {self._acme_server}")
        self._acme_server_down = False
        self._acme_server_ok = False

        self._a2md_bin = os.path.join(self.bin_dir, 'a2md')
        self._default_domain = f"test1.{self.http_tld}"
        self._store_dir = "./md"
        self.set_store_dir_default()

        self.add_cert_specs([
            CertificateSpec(domains=[f"expired.{self._http_tld}"],
                            valid_from=timedelta(days=-100),
                            valid_to=timedelta(days=-10)),
            CertificateSpec(domains=["localhost"], key_type='rsa2048'),
        ])

        if setup_dirs:
            self._setup = MDTestSetup(env=self)
            self._setup.make()
            self.issue_certs()
            self.clear_store()

    def set_store_dir_default(self):
        dirpath = "md"
        if self.httpd_is_at_least("2.5.0"):
            dirpath = os.path.join("state", dirpath)
        self.set_store_dir(dirpath)

    def set_store_dir(self, dirpath):
        self._store_dir = os.path.join(self.server_dir, dirpath)
        if self.acme_url:
            self.a2md_stdargs([self.a2md_bin, "-a", self.acme_url, "-d", self._store_dir,  "-C", self.acme_ca_pemfile, "-j"])
            self.a2md_rawargs([self.a2md_bin, "-a", self.acme_url, "-d", self._store_dir,  "-C", self.acme_ca_pemfile])

    def get_apxs_var(self, name: str) -> str:
        p = subprocess.run([self._apxs, "-q", name], capture_output=True, text=True)
        if p.returncode != 0:
            return ""
        return p.stdout.strip()

    @property
    def acme_server(self):
        return self._acme_server

    @property
    def acme_url(self):
        return self._acme_url

    @property
    def acme_tos(self):
        return self._acme_tos

    @property
    def a2md_bin(self):
        return self._a2md_bin

    @property
    def acme_ca_pemfile(self):
        return self._acme_ca_pemfile

    @property
    def store_dir(self):
        return self._store_dir

    def get_request_domain(self, request):
        return "%s-%s" % (re.sub(r'[_]', '-', request.node.originalname), MDTestEnv.DOMAIN_SUFFIX)

    def get_method_domain(self, method):
        return "%s-%s" % (re.sub(r'[_]', '-', method.__name__.lower()), MDTestEnv.DOMAIN_SUFFIX)

    def get_module_domain(self, module):
        return "%s-%s" % (re.sub(r'[_]', '-', module.__name__.lower()), MDTestEnv.DOMAIN_SUFFIX)

    def get_class_domain(self, c):
        return "%s-%s" % (re.sub(r'[_]', '-', c.__name__.lower()), MDTestEnv.DOMAIN_SUFFIX)

    # --------- cmd execution ---------

    _a2md_args = []
    _a2md_args_raw = []

    def a2md_stdargs(self, args):
        self._a2md_args = [] + args

    def a2md_rawargs(self, args):
        self._a2md_args_raw = [] + args

    def a2md(self, args, raw=False) -> ExecResult:
        preargs = self._a2md_args
        if raw:
            preargs = self._a2md_args_raw
        log.debug("running: {0} {1}".format(preargs, args))
        return self.run(preargs + args)

    def check_acme(self):
        if self._acme_server_ok:
            return True
        if self._acme_server_down:
            pytest.skip(msg="ACME server not running")
            return False
        if self.is_live(self.acme_url, timeout=timedelta(seconds=0.5)):
            self._acme_server_ok = True
            return True
        else:
            self._acme_server_down = True
            pytest.fail(msg="ACME server not running", pytrace=False)
            return False

    # --------- access local store ---------

    def purge_store(self):
        log.debug("purge store dir: %s" % self._store_dir)
        assert len(self._store_dir) > 1
        if os.path.exists(self._store_dir):
            shutil.rmtree(self._store_dir, ignore_errors=False)
        os.makedirs(self._store_dir)

    def clear_store(self):
        log.debug("clear store dir: %s" % self._store_dir)
        assert len(self._store_dir) > 1
        if not os.path.exists(self._store_dir):
            os.makedirs(self._store_dir)
        for dirpath in ["challenges", "tmp", "archive", "domains", "accounts", "staging", "ocsp"]:
            shutil.rmtree(os.path.join(self._store_dir, dirpath), ignore_errors=True)

    def clear_ocsp_store(self):
        assert len(self._store_dir) > 1
        dirpath = os.path.join(self._store_dir, "ocsp")
        log.debug("clear ocsp store dir: %s" % dir)
        if os.path.exists(dirpath):
            shutil.rmtree(dirpath, ignore_errors=True)

    def authz_save(self, name, content):
        dirpath = os.path.join(self._store_dir, 'staging', name)
        os.makedirs(dirpath)
        open(os.path.join(dirpath, 'authz.json'), "w").write(content)

    def path_store_json(self):
        return os.path.join(self._store_dir, 'md_store.json')

    def path_account(self, acct):
        return os.path.join(self._store_dir, 'accounts', acct, 'account.json')

    def path_account_key(self, acct):
        return os.path.join(self._store_dir, 'accounts', acct, 'account.pem')

    def store_domains(self):
        return os.path.join(self._store_dir, 'domains')

    def store_archives(self):
        return os.path.join(self._store_dir, 'archive')

    def store_stagings(self):
        return os.path.join(self._store_dir, 'staging')

    def store_challenges(self):
        return os.path.join(self._store_dir, 'challenges')

    def store_domain_file(self, domain, filename):
        return os.path.join(self.store_domains(), domain, filename)

    def store_archived_file(self, domain, version, filename):
        return os.path.join(self.store_archives(), "%s.%d" % (domain, version), filename)

    def store_staged_file(self, domain, filename):
        return os.path.join(self.store_stagings(), domain, filename)

    def path_fallback_cert(self, domain):
        return os.path.join(self._store_dir, 'domains', domain, 'fallback-pubcert.pem')

    def path_job(self, domain):
        return os.path.join(self._store_dir, 'staging', domain, 'job.json')

    def replace_store(self, src):
        shutil.rmtree(self._store_dir, ignore_errors=False)
        shutil.copytree(src, self._store_dir)

    def list_accounts(self):
        return os.listdir(os.path.join(self._store_dir, 'accounts'))

    def check_md(self, domain, md=None, state=-1, ca=None, protocol=None, agreement=None, contacts=None):
        domains = None
        if isinstance(domain, list):
            domains = domain
            domain = domains[0]
        if md:
            domain = md
        path = self.store_domain_file(domain, 'md.json')
        with open(path) as f:
            md = json.load(f)
        assert md
        if domains:
            assert md['domains'] == domains
        if state >= 0:
            assert md['state'] == state
        if ca:
            assert md['ca']['url'] == ca
        if protocol:
            assert md['ca']['proto'] == protocol
        if agreement:
            assert md['ca']['agreement'] == agreement
        if contacts:
            assert md['contacts'] == contacts

    def pkey_fname(self, pkeyspec=None):
        if pkeyspec and not re.match(r'^rsa( ?\d+)?$', pkeyspec.lower()):
            return "privkey.{0}.pem".format(pkeyspec)
        return 'privkey.pem'

    def cert_fname(self, pkeyspec=None):
        if pkeyspec and not re.match(r'^rsa( ?\d+)?$', pkeyspec.lower()):
            return "pubcert.{0}.pem".format(pkeyspec)
        return 'pubcert.pem'

    def check_md_complete(self, domain, pkey=None):
        md = self.get_md_status(domain)
        assert md
        assert 'state' in md, "md is unexpected: {0}".format(md)
        assert md['state'] is MDTestEnv.MD_S_COMPLETE, "unexpected state: {0}".format(md['state'])
        assert os.path.isfile(self.store_domain_file(domain, self.pkey_fname(pkey)))
        assert os.path.isfile(self.store_domain_file(domain, self.cert_fname(pkey)))

    def check_md_credentials(self, domain):
        if isinstance(domain, list):
            domains = domain
            domain = domains[0]
        else:
            domains = [domain]
        # check private key, validate certificate, etc
        MDCertUtil.validate_privkey(self.store_domain_file(domain, 'privkey.pem'))
        cert = MDCertUtil(self.store_domain_file(domain, 'pubcert.pem'))
        cert.validate_cert_matches_priv_key(self.store_domain_file(domain, 'privkey.pem'))
        # check SANs and CN
        assert cert.get_cn() == domain
        # compare lists twice in opposite directions: SAN may not respect ordering
        san_list = list(cert.get_san_list())
        assert len(san_list) == len(domains)
        assert set(san_list).issubset(domains)
        assert set(domains).issubset(san_list)
        # check valid dates interval
        not_before = cert.get_not_before()
        not_after = cert.get_not_after()
        assert not_before < datetime.now(not_before.tzinfo)
        assert not_after > datetime.now(not_after.tzinfo)

    RE_MD_RESET = re.compile(r'.*\[md:info].*initializing\.\.\.')
    RE_MD_ERROR = re.compile(r'.*\[md:error].*')
    RE_MD_WARN = re.compile(r'.*\[md:warn].*')

    def httpd_error_log_count(self, expect_errors=False, timeout_sec=5):
        ecount = 0
        wcount = 0
        end = datetime.now() + timedelta(seconds=timeout_sec)
        while datetime.now() < end:
            if os.path.isfile(self._server_error_log):
                fin = open(self._server_error_log)
                for line in fin:
                    m = self.RE_MD_ERROR.match(line)
                    if m:
                        ecount += 1
                        continue
                    m = self.RE_MD_WARN.match(line)
                    if m:
                        wcount += 1
                        continue
                    m = self.RE_MD_RESET.match(line)
                    if m:
                        ecount = 0
                        wcount = 0
            if not expect_errors or ecount + wcount > 0:
                return ecount, wcount
            time.sleep(.1)
        raise TimeoutError(f"waited {timeout_sec} sec for error/warnings to show up in log")

    def httpd_error_log_scan(self, regex):
        if not os.path.isfile(self._server_error_log):
            return False
        fin = open(self._server_error_log)
        for line in fin:
            if regex.match(line):
                return True
        return False

    RE_APLOGNO = re.compile(r'.*\[(?P<module>[^:]+):(error|warn)].* (?P<aplogno>AH\d+): .+')
    RE_ERRLOG_ERROR = re.compile(r'.*\[(?P<module>[^:]+):error].*')
    RE_ERRLOG_WARN = re.compile(r'.*\[(?P<module>[^:]+):warn].*')
    RE_NOTIFY_ERR = re.compile(r'.*\[urn:org:apache:httpd:log:AH10108:.+')
    RE_NO_RESPONDER = re.compile(r'.*has no OCSP responder URL.*')

    def apache_errors_and_warnings(self):
        errors = []
        warnings = []

        if os.path.isfile(self._server_error_log):
            for line in open(self._server_error_log):
                m = self.RE_APLOGNO.match(line)
                if m and m.group('aplogno') in [
                    'AH01873',  # ssl session cache not configured
                    'AH10085',  # warning about fallback cert active
                    'AH02217',  # ssl_stapling init error
                    'AH02604',  # ssl unable to configure our self signed for stapling
                    'AH10045',  # md reports a non match domain to vhosts
                ]:
                    # we know these happen normally in our tests
                    continue
                m = self.RE_NOTIFY_ERR.match(line)
                if m:
                    continue
                m = self.RE_NO_RESPONDER.match(line)
                if m:
                    # happens during testing
                    continue
                m = self.RE_ERRLOG_ERROR.match(line)
                if m and m.group('module') not in ['cgid']:
                    errors.append(line)
                    continue
                m = self.RE_ERRLOG_WARN.match(line)
                if m:
                    warnings.append(line)
                    continue
        return errors, warnings

    def apache_errors_check(self):
        errors, warnings = self.apache_errors_and_warnings()
        assert (len(errors), len(warnings)) == (0, 0), \
            f"apache logged {len(errors)} errors and {len(warnings)} warnings: \n" \
            "{0}\n{1}\n".format("\n".join(errors), "\n".join(warnings))

    # --------- check utilities ---------

    def check_json_contains(self, actual, expected):
        # write all expected key:value bindings to a copy of the actual data ... 
        # ... assert it stays unchanged 
        test_json = copy.deepcopy(actual)
        test_json.update(expected)
        assert actual == test_json

    def check_file_access(self, path, exp_mask):
        actual_mask = os.lstat(path).st_mode & 0o777
        assert oct(actual_mask) == oct(exp_mask)

    def check_dir_empty(self, path):
        assert os.listdir(path) == []

    def get_http_status(self, domain, path, use_https=True):
        r = self.get_meta(domain, path, use_https, insecure=True)
        return r.response['status']

    def get_cert(self, domain, tls=None, ciphers=None):
        return MDCertUtil.load_server_cert(self._httpd_addr, self.https_port,
                                           domain, tls=tls, ciphers=ciphers)

    def get_server_cert(self, domain, proto=None, ciphers=None):
        args = [
            "openssl", "s_client", "-status",
            "-connect", "%s:%s" % (self._httpd_addr, self.https_port),
            "-CAfile", self.acme_ca_pemfile,
            "-servername", domain,
            "-showcerts"
        ]
        if proto is not None:
            args.extend(["-{0}".format(proto)])
        if ciphers is not None:
            args.extend(["-cipher", ciphers])
        r = self.run(args)
        # noinspection PyBroadException
        try:
            return MDCertUtil.parse_pem_cert(r.stdout)
        except:
            return None

    def verify_cert_key_lenghts(self, domain, pkeys):
        for p in pkeys:
            cert = self.get_server_cert(domain, proto="tls1_2", ciphers=p['ciphers'])
            if 0 == p['keylen']:
                assert cert is None
            else:
                assert cert, "no cert returned for cipher: {0}".format(p['ciphers'])
                assert cert.get_key_length() == p['keylen'], "key length, expected {0}, got {1}".format(
                    p['keylen'], cert.get_key_length()
                )

    def get_meta(self, domain, path, use_https=True, insecure=False):
        schema = "https" if use_https else "http"
        port = self.https_port if use_https else self.http_port
        r = self.curl_get(f"{schema}://{domain}:{port}{path}", insecure=insecure)
        assert r.exit_code == 0
        assert r.response
        assert r.response['header']
        return r

    def get_content(self, domain, path, use_https=True):
        schema = "https" if use_https else "http"
        port = self.https_port if use_https else self.http_port
        r = self.curl_get(f"{schema}://{domain}:{port}{path}")
        assert r.exit_code == 0
        return r.stdout

    def get_json_content(self, domain, path, use_https=True, insecure=False,
                         debug_log=True):
        schema = "https" if use_https else "http"
        port = self.https_port if use_https else self.http_port
        url = f"{schema}://{domain}:{port}{path}"
        r = self.curl_get(url, insecure=insecure, debug_log=debug_log)
        if r.exit_code != 0:
            log.error(f"curl get on {url} returned {r.exit_code}"
                      f"\nstdout: {r.stdout}"
                      f"\nstderr: {r.stderr}")
        assert r.exit_code == 0, r.stderr
        return r.json

    def get_certificate_status(self, domain) -> Dict:
        return self.get_json_content(domain, "/.httpd/certificate-status", insecure=True)

    def get_md_status(self, domain, via_domain=None, use_https=True, debug_log=False) -> Dict:
        if via_domain is None:
            via_domain = self._default_domain
        return self.get_json_content(via_domain, f"/md-status/{domain}",
                                     use_https=use_https, debug_log=debug_log)

    def get_server_status(self, query="/", via_domain=None, use_https=True):
        if via_domain is None:
            via_domain = self._default_domain
        return self.get_content(via_domain, "/server-status%s" % query, use_https=use_https)

    def await_completion(self, names, must_renew=False, restart=True, timeout=60,
                         via_domain=None, use_https=True):
        try_until = time.time() + timeout
        renewals = {}
        names = names.copy()
        while len(names) > 0:
            if time.time() >= try_until:
                return False
            for name in names:
                mds = self.get_md_status(name, via_domain=via_domain, use_https=use_https)
                if mds is None:
                    log.debug("not managed by md: %s" % name)
                    return False

                if 'renewal' in mds:
                    renewal = mds['renewal']
                    renewals[name] = True
                    if 'finished' in renewal and renewal['finished'] is True:
                        if (not must_renew) or (name in renewals):
                            log.debug(f"domain cert was renewed: {name}")
                            names.remove(name)

            if len(names) != 0:
                time.sleep(0.1)
        if restart:
            time.sleep(0.1)
            return self.apache_restart() == 0
        return True

    def is_renewing(self, name):
        stat = self.get_certificate_status(name)
        return 'renewal' in stat

    def await_renewal(self, names, timeout=60):
        try_until = time.time() + timeout
        while len(names) > 0:
            if time.time() >= try_until:
                return False
            for name in names:
                md = self.get_md_status(name)
                if md is None:
                    log.debug("not managed by md: %s" % name)
                    return False

                if 'renewal' in md:
                    names.remove(name)

            if len(names) != 0:
                time.sleep(0.1)
        return True

    def await_error(self, domain, timeout=60, via_domain=None, use_https=True, errors=1):
        try_until = time.time() + timeout
        while True:
            if time.time() >= try_until:
                return False
            md = self.get_md_status(domain, via_domain=via_domain, use_https=use_https)
            if md:
                if 'state' in md and md['state'] == MDTestEnv.MD_S_ERROR:
                    return md
                if 'renewal' in md and 'errors' in md['renewal'] \
                        and md['renewal']['errors'] >= errors:
                    return md
            time.sleep(0.1)
        return None

    def await_file(self, fpath, timeout=60):
        try_until = time.time() + timeout
        while True:
            if time.time() >= try_until:
                return False
            if os.path.isfile(fpath):
                return True
            time.sleep(0.1)

    def check_file_permissions(self, domain):
        md = self.a2md(["list", domain]).json['output'][0]
        assert md
        acct = md['ca']['account']
        assert acct
        self.check_file_access(self.path_store_json(), 0o600)
        # domains
        self.check_file_access(self.store_domains(), 0o700)
        self.check_file_access(os.path.join(self.store_domains(), domain), 0o700)
        self.check_file_access(self.store_domain_file(domain, 'privkey.pem'), 0o600)
        self.check_file_access(self.store_domain_file(domain, 'pubcert.pem'), 0o600)
        self.check_file_access(self.store_domain_file(domain, 'md.json'), 0o600)
        # archive
        self.check_file_access(self.store_archived_file(domain, 1, 'md.json'), 0o600)
        # accounts
        self.check_file_access(os.path.join(self._store_dir, 'accounts'), 0o755)
        self.check_file_access(os.path.join(self._store_dir, 'accounts', acct), 0o755)
        self.check_file_access(self.path_account(acct), 0o644)
        self.check_file_access(self.path_account_key(acct), 0o644)
        # staging
        self.check_file_access(self.store_stagings(), 0o755)

    def get_ocsp_status(self, domain, proto=None, cipher=None, ca_file=None):
        stat = {}
        args = [
            "openssl", "s_client", "-status",
            "-connect", "%s:%s" % (self._httpd_addr, self.https_port),
            "-CAfile", ca_file if ca_file else self.acme_ca_pemfile,
            "-servername", domain,
            "-showcerts"
        ]
        if proto is not None:
            args.extend(["-{0}".format(proto)])
        if cipher is not None:
            args.extend(["-cipher", cipher])
        r = self.run(args, debug_log=False)
        ocsp_regex = re.compile(r'OCSP response: +([^=\n]+)\n')
        matches = ocsp_regex.finditer(r.stdout)
        for m in matches:
            if m.group(1) != "":
                stat['ocsp'] = m.group(1)
        if 'ocsp' not in stat:
            ocsp_regex = re.compile(r'OCSP Response Status:\s*(.+)')
            matches = ocsp_regex.finditer(r.stdout)
            for m in matches:
                if m.group(1) != "":
                    stat['ocsp'] = m.group(1)
        verify_regex = re.compile(r'Verify return code:\s*(.+)')
        matches = verify_regex.finditer(r.stdout)
        for m in matches:
            if m.group(1) != "":
                stat['verify'] = m.group(1)
        return stat

    def await_ocsp_status(self, domain, timeout=10, ca_file=None):
        try_until = time.time() + timeout
        while True:
            if time.time() >= try_until:
                break
            stat = self.get_ocsp_status(domain, ca_file=ca_file)
            if 'ocsp' in stat and stat['ocsp'] != "no response sent":
                return stat
            time.sleep(0.1)
        raise TimeoutError(f"ocsp respopnse not available: {domain}")

    def create_self_signed_cert(self, name_list, valid_days, serial=1000, path=None):
        dirpath = path
        if not path:
            dirpath = os.path.join(self.store_domains(), name_list[0])
        return MDCertUtil.create_self_signed_cert(dirpath, name_list, valid_days, serial)
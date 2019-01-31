/* Copyright 2019 greenbytes GmbH (https://www.greenbytes.de)
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#ifndef md_acme_drive_h
#define md_acme_drive_h

struct apr_array_header_t;
struct md_acme_order_t;

typedef struct md_acme_driver_t {
    md_proto_driver_t *driver;
    void *sub_driver;
    
    const char *phase;
    int complete;

    md_pkey_t *privkey;              /* the new private key */
    apr_array_header_t *pubcert;     /* the new certificate + chain certs */
    
    apr_array_header_t *certs;       /* the certifiacte chain, starting with the new one */
    const char *next_up_link;        /* where the next chain cert is */
    
    md_acme_t *acme;
    md_t *md;
    struct apr_array_header_t *domains;
    const md_creds_t *ncreds;
    
    apr_array_header_t *ca_challenges;
    struct md_acme_order_t *order;
    apr_interval_time_t authz_monitor_timeout;
    
    const char *csr_der_64;
    apr_interval_time_t cert_poll_timeout;
    
} md_acme_driver_t;

apr_status_t md_acme_drive_set_acct(struct md_proto_driver_t *d);
apr_status_t md_acme_drive_setup_certificate(struct md_proto_driver_t *d);
apr_status_t md_acme_drive_cert_poll(struct md_proto_driver_t *d, int only_once);

#endif /* md_acme_drive_h */


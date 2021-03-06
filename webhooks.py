# -*- coding: utf-8 -*-
#
# Copyright (C) 2014 Carlos Jenkins <carlos@jenkins.co.cr>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

import logging
import logging.config
import logging.handlers

logging.config.dictConfig(dict(
    version=1,
    formatters={'brief': {
        'format': '%(name)s %(message)s',
        'datefmt': '%Y-%m-%d %H:%M:%S'},
        'long': {
            'format': '%(name)s %(filename)s:%(lineno)d %(message)s',
        'datefmt': '%Y-%m-%d %H:%M:%S'},
        },
    handlers={'syslog': {
        'class': 'logging.handlers.SysLogHandler',
        'level': 'INFO',
        'formatter': 'brief'
    },
        "out": {
            "level": "INFO",
            "class": "logging.StreamHandler",
            "formatter": "brief",
            "stream": "ext://sys.stdout"
        },
           "err": {
            "level": "ERROR",
            "class": "logging.StreamHandler",
            "formatter": "long"
        } },
    root={'handlers': ['syslog', "out", "err"], 'level': 'INFO'}))

log = logging.getLogger('webhooks')

import hmac
from hashlib import sha1
from json import loads, dumps
from subprocess import Popen, PIPE
from tempfile import mkstemp
from os import access, X_OK, remove
from os.path import isfile, abspath, normpath, dirname, join, basename

import requests
from ipaddress import ip_address, ip_network
from flask import Flask, request, abort


application = Flask(__name__)
application.config.from_envvar('WEBHOOKS_CONFIG')


@application.route('/', methods=['GET', 'POST'])
def index():
    """
    Main WSGI application entry.
    """

    path = normpath(abspath(dirname(__file__)))
    hooks = join(path, 'hooks')

    # Only POST is implemented
    if request.method != 'POST':
        log.error("Someone is performing GET over /")
        abort(501)

    # Allow Github IPs only
    if application.config.get('GITHUB_IPS_ONLY', True):
        src_ip = ip_address(
            u'{}'.format(request.remote_addr)  # Fix stupid ipaddress issue
        )
        whitelist = requests.get('https://api.github.com/meta').json()['hooks']

        for valid_ip in whitelist:
            if src_ip in ip_network(valid_ip):
                break
        else:
            abort(403)

    # Enforce secret
    secret = application.config.get('ENFORCE_SECRET', '')
    if secret:
        # Only SHA1 is supported
        sha_name, signature = request.headers.get('X-Hub-Signature').split('=')
        if sha_name != 'sha1':
            abort(501)

        # HMAC requires the key to be bytes, but data is string
        mac = hmac.new(str(secret), msg=request.data, digestmod=sha1)
        if not hmac.compare_digest(str(mac.hexdigest()), str(signature)):
            abort(403)

    # Implement ping
    event = request.headers.get('X-GitHub-Event', None)
    if event is None:
        event = request.headers.get('X-Gitlab-Event', '')
    if event == 'ping':
        log.info("just a ping")
        # return dumps({'msg': 'pong'})

    # Gather data
    try:
        payload = loads(request.data)
        meta = {
            'name': payload['repository']['name'],
            'branch': payload['ref'].split('/')[2],
            'event': event or payload['object_kind']
        }
    except Exception:
        logging.exception("invalid payload")
        abort(400)

    # Possible hooks
    candidate_scripts = [
        join(hooks, '{event}-{name}-{branch}'.format(**meta)),
        join(hooks, '{event}-{name}'.format(**meta)),
        join(hooks, '{event}'.format(**meta)),
        join(hooks, 'all')
    ]

    # Check permissions
    scripts = [s for s in candidate_scripts if isfile(s) and access(s, X_OK)]
    if not scripts:
        log.warning("nothing found for " + candidate_scripts[0])
        return ''

    # Save payload to temporal file
    _, tmpfile = mkstemp()
    with open(tmpfile, 'w') as pf:
        pf.write(dumps(payload))

    # Run scripts
    ran = {}
    for s in scripts:

        proc = Popen(
            [s, tmpfile, event],
            stdout=PIPE, stderr=PIPE
        )
        stdout, stderr = proc.communicate()

        ran[basename(s)] = {
            'returncode': proc.returncode,
            'stdout': stdout,
            'stderr': stderr,
        }

        # Log errors if a hook failed
        if proc.returncode != 0:
            log.error('{} : {} \n{}'.format(
                s, proc.returncode, stderr
            ))

    # Remove temporal file
    remove(tmpfile)

    info = application.config.get('RETURN_SCRIPTS_INFO', False)
    if not info:
        return ''

    output = dumps(ran, sort_keys=True)
    log.info(output)
    return output


if __name__ == '__main__':
    application.run(debug=True, host='0.0.0.0',
                    port=application.config['PORT'])

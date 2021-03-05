
from __future__ import absolute_import
from __future__ import print_function

import re
from pprint import pprint

from twisted.internet import defer
from twisted.python import log

from buildbot.process.properties import Interpolate
from buildbot.process.properties import Properties
from buildbot.process.results import CANCELLED
from buildbot.process.results import EXCEPTION
from buildbot.process.results import FAILURE
from buildbot.process.results import RETRY
from buildbot.process.results import SKIPPED
from buildbot.process.results import SUCCESS
from buildbot.process.results import WARNINGS
from buildbot.process.results import statusToString
from buildbot.reporters.base import ReporterBase
from buildbot.reporters.generators.buildset import BuildSetStatusGenerator
from buildbot.reporters.generators.build import BuildStatusGenerator
from buildbot.reporters.generators.worker import WorkerMissingGenerator
from buildbot.util import httpclientservice

# Fix a bug in BuildStatusGeneratorMixin which misses _matches_any_tag
try:
    BuildSetStatusGenerator._matches_any_tag
except AttributeError:
    import buildbot.reporters.generators.utils
    import buildbot.reporters.generators.build
    buildbot.reporters.generators.utils.BuildStatusGeneratorMixin._matches_any_tag = buildbot.reporters.generators.build.BuildStatusGenerator._matches_any_tag

import re

class MatrixStatusPush(ReporterBase):
    name = "MatrixStatusPush"
    neededDetails = dict(wantProperties=True)
    ssh_url_match = re.compile(r"(ssh://)?[\w+\.\-\_]+:?(\d*/)?(?P<owner>[\w_\-\.]+)/(?P<repo_name>[\w_\-\.]+)(\.git)?")

    def checkConfig(
            self,
            homeserverURL,
            room_id,
            access_token,
            startDescription=None,
            endDescription=None,
            context=None,
            context_pr=None,
            verbose=False,
            warningAsSuccess=False,
            onlyEndState=False,
            generators=None,
            debug=None,
            verify=None,
            **kwargs
        ):
        log.msg('CHECKCONFIG')
        
        # if generators is None:
            # generators = [BuildSetStatusGenerator()]
            
        super().checkConfig(generators=generators, **kwargs)



    @defer.inlineCallbacks
    def reconfigService(
            self,
            homeserverURL,
            room_id,
            access_token,
            startDescription=None,
            endDescription=None,
            context=None,
            context_pr=None,
            verbose=False,
            warningAsSuccess=False,
            onlyEndState=False,
            generators=None,
            debug=None,
            verify=None,
            **kwargs
            ):
        
        log.msg('RECONFIG SERVER')
        
        # if generators is None:
            # generators = [BuildSetStatusGenerator()]
        
        self.access_token = yield self.renderSecrets(access_token)
        
        yield super().reconfigService(generators=generators, *kwargs)

        self.context = context or Interpolate('buildbot/%(prop:buildername)s')
        self.context_pr = context_pr or Interpolate('buildbot/pull_request/%(prop:buildername)s')
        self.startDescription = startDescription or 'Build started.'
        self.endDescription = endDescription or 'Build done.'

        self.verbose = verbose
        self.warningAsSuccess = warningAsSuccess
        self.onlyEndState = onlyEndState
        self.project_ids = {}

        if homeserverURL.endswith('/'):
            homeserverURL = homeserverURL[:-1]
        self.homeserverURL = homeserverURL
        self.room_id = room_id

        self._http = yield httpclientservice.HTTPClientService.getService(
                self.master,
                homeserverURL,
                debug=debug,
                verify=verify
            )

    def createStatus(
            self,
            project_owner,
            repo_name,
            sha,
            state,
            target_url=None,
            description=None,
            context=None
            ):
        log.msg('CREATE STATUS')

        if description is None:
            description = "No Description"
        if target_url is None:
            target_url = " "
        if context is None:
            context = "No Context"

        if state == 'success':
            color = '#00d032'
        elif state == 'warning':
            color = '#ff4500'
        elif state == 'failure':
            color = '#a71010'
        elif state == 'pending':
            color = '#67d3ff'
        elif state == 'error':
            color = '#a71010'
        else:
            color = '#bcbcb5'

        payload = {'msgtype': 'm.text'}
        payload['format'] = 'org.matrix.custom.html'
        payload['body'] = '{context}: {state} on {repo} by {name} More Info: {url}'.format(
            context=context,
            state=state,
            url=target_url,
            name=project_owner,
            repo=repo_name
        )
        payload['formatted_body'] = '[<a href=\"{url}\">{context}</a>] {state}<blockquote data-mx-border-color=\"{color}\"><h4>{context}: {state}</h4>{description}<br>Running on {repo}/{sha} by {name}<br></blockquote>'.format(
            context=context,
            state=state,
            url=target_url,
            color=color,
            description=description,
            name=project_owner,
            repo=repo_name,
            sha=sha
        )
        return self._http.post(
                '/_matrix/client/r0/rooms/{room}/send/m.room.message?access_token={token}'.format(
                    room=self.room_id,
                    token=self.access_token
                ),
                json=payload)

    @defer.inlineCallbacks
    def sendMessage(self, reports):
        log.msg('SEND MESSAGE')
        
        pprint(reports)
        
        
        for report in reports:
            if report['type'] != 'plain':
                log.msg("MatrixStatusPush: got report of unexpected type {}".format(report['type']))
                continue
                
            report['results_text'] = statusToString(report['results'])
            log.msg(report['results_text'])

        
            props = Properties.fromDict(report['builds']['properties'])
            props.master = self.master
    
            if report['builds']['complete']:
                state = {
                        SUCCESS: 'success',
                        WARNINGS: 'success' if self.warningAsSuccess else 'warning',
                        FAILURE: 'failure',
                        SKIPPED: 'success',
                        EXCEPTION: 'error',
                        RETRY: 'pending',
                        CANCELLED: 'error'
                    }.get(reports['builds']['buildset']['results'], 'failure')
                description = yield props.render(self.endDescription)
            else:
                state = 'pending'
                description = yield props.render(self.startDescription)
    
            if 'pr_id' in props:
                context = yield props.render(self.context_pr)
            else:
                context = yield props.render(self.context)
    
            sourcestamps = reports['builds']['buildset']['sourcestamps']
            for sourcestamp in sourcestamps:
                sha = sourcestamp['revision']
                if sha is None:
                    continue
                if 'repository_name' in props:
                    repository_name = props['repository_name']
                else:
                    match = re.match(self.ssh_url_match, sourcestamp['repository'])
                    if match is not None:
                        repository_name = match.group("repo_name")
                    else:
                        repository_name = None
    
                if 'owner' in props:
                    repository_owner = props['owner']
                else:
                    match = re.match(self.ssh_url_match, sourcestamp['repository'])
                    if match is not None:
                        repository_owner = match.group("owner")
                    else:
                        repository_owner = None
    
                if (state == 'pending') and (self.onlyEndState):
                    log.msg('Pending message not set to matrix, as configured')
                    return
                else:
                    try:
                        target_url = reports['builds']['url']
                        result = yield self.createStatus(
                                project_owner=repository_owner,
                                repo_name=repository_name,
                                sha=sha,
                                state=state,
                                target_url=target_url,
                                context=context,
                                description=description
                            )
                        if result.code not in (200, 201, 204):
                            message = yield result.json()
                            message = message.get('message', 'unspecified error')
                            log.msg('Code: {code} - Could not send Notification: {message}'.format(code=result.code, message=message))
                        elif self.verbose:
                            log.msg('Notification send to {room}'.format(room=self.room_id))
                    except Exception as e:
                        log.err(e, 'Failed to send notification to {room}'.format(room=self.room_id))

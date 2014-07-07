import httplib
from pecan import (abort, expose)
from pecan.rest import RestController

from wsme import types as wstypes
import wsmeext.pecan as wsme_pecan

from st2common import log as logging

from st2common.exceptions.db import StackStormDBObjectNotFoundError
from st2common.models.api.action import ACTIONEXEC_STATUS_RUNNING
from st2common.models.api.actionrunner import LiveActionAPI
from st2common.persistence.actionrunner import LiveAction
from st2common.util.action_db import (get_actionexec_by_id, get_action_by_dict,
                                      update_actionexecution_status)
from st2common.util.actionrunner_db import (get_actiontype_by_name,
                                            get_liveaction_by_id,
                                            get_liveactions_by_actionexec_id)

from st2actionrunnercontroller.controllers import runner_container


LOG = logging.getLogger(__name__)


class LiveActionsController(RestController):
    """
        Implements the RESTful web endpoint that handles
        the lifecycle of ActionRunners in the system.
    """

    @wsme_pecan.wsexpose(LiveActionAPI, wstypes.text)
    def get_one(self, id):
        """
            List LiveAction by id.

            Handle:
                GET /liveactions/1
        """

        LOG.info('GET /liveactions/ with id=%s', id)

        try:
            liveaction_db = get_liveaction_by_id(id)
        except StackStormDBObjectNotFoundError, e:
            LOG.error('GET /liveactions/ with id="%s": %s', id, e.message)
            abort(httplib.NOT_FOUND)

        liveaction_api = LiveActionAPI.from_model(liveaction_db)

        LOG.debug('GET /liveactions/ with id=%s, client_result=%s', id, liveaction_api)
        return liveaction_api

    @wsme_pecan.wsexpose([LiveActionAPI])
    def get_all(self):
        """
            List all liveactions.

            Handles requests:
                GET /liveactions/
        """

        LOG.info('GET all /liveactions/')

        liveaction_apis = [LiveActionAPI.from_model(liveaction_db)
                           for liveaction_db in LiveAction.get_all()]

        # TODO: unpack list in log message
        LOG.debug('GET all /liveactions/ client_result=%s', liveaction_apis)
        return liveaction_apis

    # @expose('json')
    # def post(self, **kwargs):
    @wsme_pecan.wsexpose(LiveActionAPI, body=LiveActionAPI, status_code=httplib.CREATED)
    def post(self, liveaction):
        """
            Create a new LiveAction.

            Handles requests:
                POST /liveactions/
        """
        LOG.info('POST /liveactions/ with liveaction data=%s', liveaction)

        # Validate incoming API object
        liveaction_api = LiveActionAPI.to_model(liveaction)
        LOG.debug('/liveactions/ POST verified LiveActionAPI object=%s',
                  liveaction_api)

        # To launch a LiveAction we need:
        #     1. ActionExecution object
        #     2. Action object
        #     3. ActionType object
        LOG.info('POST /liveactions/ received actionexecution_id: %s. '
                 'Attempting to obtain ActionExecution object from database.',
                 str(liveaction.actionexecution_id))
        try:
            actionexec_db = get_actionexec_by_id(liveaction.actionexecution_id)
        except StackStormDBObjectNotFoundError, e:
            LOG.error(e.message)
            # TODO: Is there a more appropriate status code?
            abort(httplib.BAD_REQUEST)

        #  Got ActionExecution object (1)
        LOG.info('POST /liveactions/ obtained ActionExecution object from database. '
                 'Object is %s', actionexec_db)

        try:
            LOG.debug('actionexecution.action value: %s', actionexec_db.action)
            (action_db, d) = get_action_by_dict(actionexec_db.action)
        except StackStormDBObjectNotFoundError, e:
            LOG.error(e.message)
            # TODO: Is there a more appropriate status code?
            abort(httplib.BAD_REQUEST)

        #  Got Action object (2)
        LOG.info('POST /liveactions/ obtained Action object from database. '
                 'Object is %s', action_db)

        # If the Action is disabled, abort the POST call.
        if not action_db.enabled:
            LOG.error('POST /actionexecutions/ Unable to create Live Action for a disabled '
                      'Action. Action is: %s', action_db)
            abort(httplib.FORBIDDEN)

        try:
            actiontype_db = get_actiontype_by_name(action_db.runner_type)
        except StackStormDBObjectNotFoundError, e:
            LOG.error(e.message)
            # TODO: Is there a more appropriate status code?
            abort(httplib.BAD_REQUEST)

        #  Got ActionType object (3)
        LOG.info('POST /liveactions/ obtained ActionType object from database. '
                 'Object is %s', actiontype_db)

        # Save LiveAction to DB
        liveaction_db = LiveAction.add_or_update(liveaction_api)
        LOG.info('POST /liveactions/ LiveAction object saved to DB. '
                 'Object is: %s', liveaction_db)

        # Update ActionExecution status to "running"
        actionexec_db = update_actionexecution_status(ACTIONEXEC_STATUS_RUNNING,
                                                      actionexec_db.id)
        # Launch action
        LOG.debug('Launching LiveAction command.')
        global runner_container
        result = runner_container.dispatch(liveaction_db, actiontype_db, action_db, actionexec_db)
        LOG.debug('Runner dispatch produced result: %s', result)

        if not result:
            # Return different code for live action execution failure
            abort(httplib.INTERNAL_SERVER_ERROR)

        """
        LOG.info('Update ActionExecution object with Action result data')
        actionexec_db.exit_code = str(exit_code)
        actionexec_db.std_out = str(json.dumps(std_out))
        actionexec_db.std_err = str(json.dumps(std_err))
        actionexec_db = ActionExecution.add_or_update(actionexec_db)
        LOG.info('ActionExecution object after exit_code update: %s', actionexec_db)
        """

        liveaction_api = LiveActionAPI.from_model(liveaction_db)

        LOG.debug('POST /liveactions/ client_result=%s', liveaction_api)
        return liveaction_api

    @expose('json')
    def put(self, id, **kwargs):
        """
            Update not supported for LiveActions.

            Handles requests:
                POST /liveactions/1?_method=put
                PUT /liveactions/1
        """
        abort(httplib.METHOD_NOT_ALLOWED)

    @wsme_pecan.wsexpose(None, wstypes.text, wstypes.text)
    def delete(self, id, actionexecution_id=None):
        """
            Delete Live Actions.

            Handles requests:
                POST /liveactions/1?_method=delete
                DELETE /liveactions/1
                DELETE /liveactions/?actionexecution_id=2

            Note: Delete by actionexecution_id may delete multiple Live Action objects.
        """

        # TODO: Handle delete to ensure object is not re-created by action runner.

        actionexec_id = actionexecution_id
        LOG.info('DELETE /liveactions/ with id="%s" and actionexecution_id="%s"',
                 id, actionexec_id)

        if (id) and (actionexec_id):
            LOG.error('DELETE /liveactions/ request is invalid. Can only specify liveaction id, '
                      ' or actionexecution_id.')
            abort(httplib.BAD_REQUEST)

        liveactions_db = []

        if id:
            try:
                db = get_liveaction_by_id(id)
            except StackStormDBObjectNotFoundError, e:
                LOG.error('DELETE /liveactions/ with id="%s": %s', id, e.message)
                abort(httplib.NOT_FOUND)

            liveactions_db.append(db)
        elif actionexec_id:
            try:
                dbs = get_liveactions_by_actionexec_id(actionexec_id)
            except StackStormDBObjectNotFoundError, e:
                LOG.error('DELETE /liveactions/ with actionexecution_id="%s": %s',
                          actionexec_id, e.message)
                abort(httplib.NOT_FOUND)
            liveactions_db.extend(dbs)
        else:
            LOG.error('DELETE /liveactions/ unknown identifier provided')
            abort(httplib.BAD_REQUEST)

        LOG.debug('DELETE /liveactions/ lookup found objects: %s', liveactions_db)

        if not liveactions_db:
            LOG.error('DELETE /liveactions/ found no objects to delete.')
            abort(httplib.NOT_FOUND)

        for liveaction_db in liveactions_db:
            try:
                LiveAction.delete(liveaction_db)
            except Exception, e:
                LOG.error('Database delete encountered exception during delete of LiveAction: '
                          '"%s". Exception was %s', liveaction_db, e)
                httplib.INTERNAL_SERVER_ERROR

        LOG.info('DELETE /liveactions/ compeleted.')
        return None

"""
Functions needed to synchronise data with the SecureDrop server (via the
securedrop_sdk). Each function requires but two arguments: an authenticated
instance of the securedrop_sdk API class, and a SQLAlchemy session to the local
database.

Copyright (C) 2018  The Freedom of the Press Foundation.

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published
by the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
import logging
from dateutil.parser import parse
from securedrop_client.models import Source, Submission, Reply, User


logger = logging.getLogger(__name__)


def sync_with_api(api, session):
    """
    Synchronises sources and submissions from the remote server's API.
    """
    remote_submissions = []
    try:
        remote_sources = api.get_sources()
        for source in remote_sources:
            remote_submissions.extend(api.get_submissions(source))
        remote_replies = api.get_all_replies()
    except Exception as ex:
        # Log any errors but allow the caller to handle the exception.
        logger.error(ex)
        raise(ex)
    logger.info('Fetched {} remote sources.'.format(len(remote_sources)))
    logger.info('Fetched {} remote submissions.'.format(
        len(remote_submissions)))
    logger.info('Fetched {} remote replies.'.format(len(remote_replies)))
    local_sources = session.query(Source)
    local_submissions = session.query(Submission)
    local_replies = session.query(Reply)
    update_sources(remote_sources, local_sources, session)
    update_submissions(remote_submissions, local_submissions, session)
    update_replies(remote_replies, local_replies, session)


def update_sources(remote_sources, local_sources, session):
    """
    Given collections of remote sources, the current local sources and a
    session to the local database, ensure the state of the local database
    matches that of the remote sources:

    * Existing items are updated in the local database.
    * New items are created in the local database.
    * Local items not returned in the remote sources are deleted from the
      local database.
    """
    local_uuids = {source.uuid for source in local_sources}
    for source in remote_sources:
        if source.uuid in local_uuids:
            # Update an existing record.
            local_source = [s for s in local_sources
                            if s.uuid == source.uuid][0]
            local_source.journalist_designation = source.journalist_designation
            local_source.is_flagged = source.is_flagged
            local_source.public_key = source.key
            local_source.interaction_count = source.interaction_count
            local_source.is_starred = source.is_starred
            local_source.last_updated = parse(source.last_updated)
            # Removing the UUID from local_uuids ensures this record won't be
            # deleted at the end of this function.
            local_uuids.remove(source.uuid)
            logger.info('Updated source {}'.format(source.uuid))
        else:
            # A new source to be added to the database.
            ns = Source(uuid=source.uuid,
                        journalist_designation=source.journalist_designation,
                        is_flagged=source.is_flagged,
                        public_key=source.key,
                        interaction_count=source.interaction_count,
                        is_starred=source.is_starred,
                        last_updated=parse(source.last_updated))
            session.add(ns)
            logger.info('Added new source {}'.format(source.uuid))
    # The uuids remaining in local_uuids do not exist on the remote server, so
    # delete the related records.
    for deleted_source in [s for s in local_sources if s.uuid in local_uuids]:
        session.delete(deleted_source)
        logger.info('Deleted source {}'.format(deleted_source.uuid))
    session.commit()


def update_submissions(remote_submissions, local_submissions, session):
    """
    * Existing submissions are updated in the local database.
    * New submissions have an entry created in the local database.
    * Local submissions not returned in the remote submissions are deleted
      from the local database.
    """
    local_uuids = {submission.uuid for submission in local_submissions}
    for submission in remote_submissions:
        if submission.uuid in local_uuids:
            # Update an existing record.
            local_submission = [s for s in local_submissions
                                if s.uuid == submission.uuid][0]
            local_submission.filename = submission.filename
            local_submission.size = submission.size
            local_submission.is_read = submission.is_read
            # Removing the UUID from local_uuids ensures this record won't be
            # deleted at the end of this function.
            local_uuids.remove(submission.uuid)
            logger.info('Updated submission {}'.format(submission.uuid))
        else:
            # A new submission to be added to the database.
            _, source_uuid = submission.source_url.rsplit('/', 1)
            source = session.query(Source).filter_by(uuid=source_uuid)[0]
            ns = Submission(source=source, uuid=submission.uuid,
                            filename=submission.filename)
            session.add(ns)
            logger.info('Added new submission {}'.format(submission.uuid))
    # The uuids remaining in local_uuids do not exist on the remote server, so
    # delete the related records.
    for deleted_submission in [s for s in local_submissions
                               if s.uuid in local_uuids]:
        session.delete(deleted_submission)
        logger.info('Deleted submission {}'.format(deleted_submission.uuid))
    session.commit()


def update_replies(remote_replies, local_replies, session):
    """
    * Existing replies are updated in the local database.
    * New replies have an entry created in the local database.
    * Local replies not returned in the remote replies are deleted from the
      local database.

    If a reply references a new journalist username, add them to the database
    as a new user.
    """
    local_uuids = {reply.uuid for reply in local_replies}
    for reply in remote_replies:
        if reply.uuid in local_uuids:
            # Update an existing record.
            local_reply = [r for r in local_replies if r.uuid == reply.uuid][0]
            user = find_or_create_user(reply.journalist_username, session)
            local_reply.journalist_id = user.id
            local_reply.filename = reply.filename
            local_reply.size = reply.size
            local_uuids.remove(reply.uuid)
            logger.info('Updated reply {}'.format(reply.uuid))
        else:
            # A new reply to be added to the database.
            source_uuid = reply.source_uuid
            source = session.query(Source).filter_by(uuid=source_uuid)[0]
            user = find_or_create_user(reply.journalist_username, session)
            nr = Reply(reply.uuid, user, source, reply.filename, reply.size)
            session.add(nr)
            logger.info('Added new reply {}'.format(reply.uuid))
    # The uuids remaining in local_uuids do not exist on the remote server, so
    # delete the related records.
    for deleted_reply in [r for r in local_replies if r.uuid in local_uuids]:
        session.delete(deleted_reply)
        logger.info('Deleted reply {}'.format(deleted_reply.uuid))
    session.commit()


def find_or_create_user(username, session):
    """
    Returns a user object representing the referenced username. If the username
    does not already exist in the data, a new instance is created.
    """
    user = session.query(User).filter_by(username=username)
    if user:
        return user[0]
    new_user = User(username)
    session.add(new_user)
    session.commit()
    return new_user

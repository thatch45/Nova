# -*- encoding: utf-8 -*-
'''
Hubble Nova plugin for running arbitrary commands and checking the output of
those commands

:maintainer: HubbleStack
:maturity: 20160516
:platform: All
:requires: SaltStack

Sample YAML data, with inline comments:

# Top level key lets the module know it should look at this data
command:
  # Unique ID for this set of audits
  nodev:
    data:
      # 'osfinger' grain, for multiplatform support
      'Red Hat Enterprise Linux Server-6':
        # tag is required
        tag: CIS-1.1.10
        # `commands` is a list of commands with individual flags
        commands:
          # Command to be run
          - 'grep "[[:space:]]/home[[:space:]]" /etc/fstab':
              # Check the output for this pattern
              # If match_output not provided, any output will be a match
              match_output: nodev
              # Use regex when matching the output (default False)
              match_output_regex: False
              # Invert the success criteria. If True, a match will cause failure (default False)
              fail_if_matched: False
          - 'mount | grep /home':
              match_output: nodev
              match_output_regex: False
              # Match each line of the output against our pattern
              # Any that don't match will make the audit fail (default False)
              match_output_by_line: True
        # Aggregation strategy for multiple commands. Defaults to 'and', other option is 'or'
        aggregation: 'and'
      # Catch-all, if no other osfinger match was found
      '*':
        tag: generic_tag
        commands:
          - 'grep "[[:space:]]/home[[:space:]]" /etc/fstab':
              match_output: nodev
              match_output_regex: False
              fail_if_matched: False
          - 'mount | grep /home':
              match_output: nodev
              match_output_regex: False
              match_output_by_line: True
        aggregation: 'and'
    # Description will be output with the results
    description: '/home should be nodev'
'''
from __future__ import absolute_import
import logging

import fnmatch
import yaml
import os
import copy
import re
import salt.utils

log = logging.getLogger(__name__)


def __virtual__():
    if salt.utils.is_windows():
        return False, 'This audit module only runs on linux'
    return True


def audit(data_list, tags, verbose=False):
    '''
    Run the command audits contained in the data_list
    '''
    __data__ = {}
    for data in data_list:
        _merge_yaml(__data__, data)
    __tags__ = _get_tags(__data__)

    log.trace('command audit __data__:')
    log.trace(__data__)
    log.trace('command audit __tags__:')
    log.trace(__tags__)

    ret = {'Success': [], 'Failure': [], 'Controlled': []}
    for tag in __tags__:
        if fnmatch.fnmatch(tag, tags):
            for tag_data in __tags__[tag]:
                if 'control' in tag_data:
                    ret['Controlled'].append(tag_data)
                    continue
                if 'commands' not in tag_data:
                    continue
                command_results = []
                for command_data in tag_data['commands']:
                    for command, command_args in command_data.iteritems():
                        cmd_ret = __salt__['cmd.run'](command, python_shell=True)

                        found = False
                        if cmd_ret:
                            found = True

                        if 'match_output' in command_args:

                            if command_args.get('match_output_by_line'):
                                cmd_ret_lines = cmd_ret.splitlines()
                            else:
                                cmd_ret_lines = [cmd_ret]

                            for line in cmd_ret_lines:
                                if command_args.get('match_output_regex'):
                                    if not re.match(command_args['match_output'], line):
                                        found = False
                                else:  # match without regex
                                    if command_args['match_output'] not in line:
                                        found = False

                        if command_args.get('fail_if_matched'):
                            found = not found

                        command_results.append(found)

                aggregation = tag_data.get('aggregation', 'and')

                if aggregation.lower() == 'or':
                    if any(command_results):
                        ret['Success'].append(tag_data)
                    else:
                        ret['Failure'].append(tag_data)
                else:  # assume 'and' if it's not 'or'
                    if all(command_results):
                        ret['Success'].append(tag_data)
                    else:
                        ret['Failure'].append(tag_data)

    if not verbose:
        failure = []
        success = []
        controlled = []

        tags_descriptions = set()

        for tag_data in ret['Failure']:
            tag = tag_data['tag']
            description = tag_data.get('description')
            if (tag, description) not in tags_descriptions:
                failure.append({tag: description})
                tags_descriptions.add((tag, description))

        tags_descriptions = set()

        for tag_data in ret['Success']:
            tag = tag_data['tag']
            description = tag_data.get('description')
            if (tag, description) not in tags_descriptions:
                success.append({tag: description})
                tags_descriptions.add((tag, description))

        control_reasons = set()

        for tag_data in ret['Controlled']:
            tag = tag_data['tag']
            control_reason = tag_data.get('control', '')
            description = tag_data.get('description')
            if (tag, description, control_reason) not in control_reasons:
                tag_dict = {'description': description,
                        'control': control_reason}
                controlled.append({tag: tag_dict})
                control_reasons.add((tag, description, control_reason))

        ret['Controlled'] = controlled
        ret['Success'] = success
        ret['Failure'] = failure

    if not ret['Controlled']:
        ret.pop('Controlled')

    return ret


def _merge_yaml(ret, data):
    '''
    Merge two yaml dicts together at the command level
    '''
    if 'command' not in ret:
        ret['command'] = []
    if 'command' in data:
        for key, val in data['command'].iteritems():
            ret['command'].append({key: val})
    return ret


def _get_tags(data):
    '''
    Retrieve all the tags for this distro from the yaml
    '''
    ret = {}
    distro = __grains__.get('osfinger')
    for audit_dict in data.get('command', []):
        # command:0
        for audit_id, audit_data in audit_dict.iteritems():
            # command:0:nodev
            tags_dict = audit_data.get('data', {})
            # command:0:nodev:data
            tags = None
            for osfinger in tags_dict:
                if osfinger == '*':
                    continue
                osfinger_list = [finger.strip() for finger in osfinger.split(',')]
                for osfinger_glob in osfinger_list:
                    if fnmatch.fnmatch(distro, osfinger_glob):
                        tags = tags_dict.get(osfinger)
                        break
                if tags is not None:
                    break
            # If we didn't find a match, check for a '*'
            if tags is None:
                tags = tags_dict.get('*', {})
            # command:0:nodev:data:Debian-8
            if 'tag' not in tags:
                tags['tag'] = ''
            tag = tags['tag']
            if tag not in ret:
                ret[tag] = []
            formatted_data = {'tag': tag,
                              'module': 'command'}
            formatted_data.update(audit_data)
            formatted_data.update(tags)
            formatted_data.pop('data')
            ret[tag].append(formatted_data)
    return ret

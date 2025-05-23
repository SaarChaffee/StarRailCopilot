from datetime import datetime, timedelta

from module.config.stored.classes import now
from module.config.utils import get_server_next_update
from module.logger import logger
from tasks.assignment.claim import AssignmentClaim
from tasks.assignment.keywords import (KEYWORDS_ASSIGNMENT_GROUP,
                                       AssignmentEntry, AssignmentEventEntry,
                                       AssignmentEventGroup)
from tasks.assignment.ui import ASSIGNMENT_ENTRY_LIST, AssignmentStatus
from tasks.base.page import page_assignment, page_menu
from tasks.daily.keywords import KEYWORDS_DAILY_QUEST
from tasks.daily.synthesize import SynthesizeUI


class Assignment(AssignmentClaim, SynthesizeUI):
    def run(self, assignments: list[AssignmentEntry] = None, duration: int = None, join_event: bool = None):
        self.config.update_battle_pass_quests()
        self.config.update_daily_quests()

        if assignments is None:
            assignments = (
                getattr(self.config, f'Assignment_Name_{i + 1}', None) for i in range(4))
            # remove duplicate while keeping order
            assignments = list(dict.fromkeys(
                x for x in assignments if x is not None))
            assignments = [AssignmentEntry.find(x) for x in assignments]
            if len(assignments) < 4:
                logger.warning(
                    'There are duplicate assignments in config, check it out')
        if duration is None:
            duration = self.config.Assignment_Duration
        if join_event is None:
            join_event = self.config.Assignment_Event

        switched = self.ui_ensure(page_menu)
        if not switched:
            self.ensure_scroll_top(page_menu, skip_first_screenshot=True)
        self.ui_goto(page_assignment)

        self.dispatched = dict()
        self.has_new_dispatch = False

        # Try claim all first
        if self.config.Assignment_ClaimAll:
            self.claim_all()

        ASSIGNMENT_ENTRY_LIST.cur_buttons = []
        event_ongoing = next((
            g for g in self._iter_groups()
            if isinstance(g, AssignmentEventGroup)
        ), None)
        if join_event and event_ongoing is not None:
            if self._check_event():
                self._check_event()

        # Full-fill assignment
        if len(self.dispatched) < 4:
            # Iterate in user-specified order, return undispatched ones
            undispatched = list(self._check_inlist(assignments, duration))
            remain = self._check_all()
            undispatched = [x for x in undispatched if x not in self.dispatched]
            # There are unchecked assignments
            if remain > 0:
                for assignment in undispatched[:remain]:
                    self.goto_entry(assignment)
                    self.dispatch(assignment, duration)
                if remain < len(undispatched):
                    logger.warning('The following assignments can not be dispatched due to limit: '
                                   f'{", ".join([x.name for x in undispatched[remain:]])}')
                elif remain > len(undispatched):
                    self._dispatch_remain(duration, remain - len(undispatched))

        # Refresh dashboard before return
        _ = self._limit_status
        # Scheduler
        logger.attr('has_new_dispatch', self.has_new_dispatch)
        with self.config.multi_set():
            # Check daily
            quests = self.config.stored.DailyQuest.load_quests()
            if KEYWORDS_DAILY_QUEST.Dispatch_1_assignments in quests:
                logger.info('Achieved daily quest Dispatch_1_assignments')
                self.config.task_call('DailyQuest')
            # Delay self
            if len(self.dispatched):
                delay = min(self.dispatched.values())
                logger.info(f'Delay assignment check to {str(delay)}')
                self.config.task_delay(target=delay)
                # Align server update
                update = get_server_next_update(self.config.Scheduler_ServerUpdate)
                if update - delay < timedelta(hours=4):
                    logger.info('Approaching next day, delay to server update instead')
                    self.config.task_delay(target=update)
            else:
                # ValueError: min() arg is an empty sequence
                logger.error('Empty dispatched list, delay 2 hours instead')
                self.config.task_delay(minute=120)
                # Check future daily
                if now() > get_server_next_update(self.config.Scheduler_ServerUpdate) - timedelta(minutes=110) \
                        and KEYWORDS_DAILY_QUEST.Dispatch_1_assignments in quests:
                    logger.error(
                        "Assigment is scheduled tomorrow but today's assignment daily haven't been finished yet")
                    self.config.task_call('DailyQuest')

    def _check_inlist(self, assignments: list[AssignmentEntry], duration: int):
        """
        Dispatch assignments according to user config

        Args:
            assignments (list[AssignmentEntry]): user specified assignments
            duration (int): user specified duration
        """
        if not assignments:
            return
        logger.hr('Assignment check inlist', level=1)
        logger.info(
            f'User specified assignments: {", ".join([x.name for x in assignments])}')
        remain = None
        insight = False
        for assignment in assignments:
            if assignment in self.dispatched:
                continue
            logger.hr('Assignment inlist', level=2)
            logger.info(f'Check assignment inlist: {assignment}')
            self.goto_entry(assignment, insight=insight)
            insight = True
            if remain is None:
                _, remain, _ = self._limit_status
            status = self._check_assignment_status()
            if status == AssignmentStatus.CLAIMABLE:
                self.claim(assignment, duration, should_redispatch=True)
                continue
            if status == AssignmentStatus.DISPATCHED:
                self.dispatched[assignment] = datetime.now() + \
                    self._get_assignment_time()
                insight = False
                continue
            # General assignments must be dispatchable here
            if remain <= 0:
                yield assignment
                continue
            self.dispatch(assignment, duration)
            remain -= 1

    def _check_all(self):
        """
        States of assignments from top to bottom are in following order:
            1. Claimable
            2. Dispatched
            3. Dispatchable
        Break when a dispatchable assignment is encountered
        """
        logger.hr('Assignment check all', level=1)
        current, remain, _ = self._limit_status
        len_dispatched = len([
            x for x in self.dispatched.keys()
            if not isinstance(x, AssignmentEventEntry)
        ])
        # current = #Claimable + #Dispatched
        if current == len_dispatched:
            return remain
        for group in self._iter_groups():
            if isinstance(group, AssignmentEventGroup):
                continue
            self.goto_group(group)
            insight = False
            for assignment in self._iter_entries():
                if assignment in self.dispatched:
                    continue
                logger.hr('Assignment all', level=2)
                logger.info(f'Check assignment all: {assignment}')
                self.goto_entry(assignment, insight=insight)
                status = self._check_assignment_status()
                if status == AssignmentStatus.CLAIMABLE:
                    self.claim(assignment, None, should_redispatch=False)
                    current -= 1
                    remain += 1
                    insight = True  # Order of entries change after claiming
                    if current == len_dispatched:
                        return remain
                    continue
                if status == AssignmentStatus.DISPATCHED:
                    self.dispatched[assignment] = datetime.now() + \
                        self._get_assignment_time()
                    len_dispatched += 1
                    insight = False  # Order of entries does not change here
                    if current == len_dispatched:
                        return remain
                    continue
                break
        return remain

    def _dispatch_remain(self, duration: int, remain: int):
        """
        Dispatch assignments according to preset priority

        Args:
            duration (int): user specified duration
            remain (int): 
                The number of remaining assignments after
                processing the ones specified by user
        """
        if remain <= 0:
            return
        logger.hr('Assignment dispatch remain', level=2)
        logger.warning(f'{remain} remain')
        logger.info(
            'Dispatch remaining assignments according to preset priority')
        group_priority = (
            KEYWORDS_ASSIGNMENT_GROUP.EXP_Materials_Credits,
            KEYWORDS_ASSIGNMENT_GROUP.Character_Materials,
            KEYWORDS_ASSIGNMENT_GROUP.Synthesis_Materials
        )
        for group in group_priority:
            for assignment in group.entries:
                if assignment in self.dispatched:
                    continue
                self.goto_entry(assignment)
                self.dispatch(assignment, duration)
                remain -= 1
                if remain <= 0:
                    return

    def _check_event(self):
        logger.hr('Assignment check event', level=1)
        claimed = False
        for group in self._iter_groups():
            if not isinstance(group, AssignmentEventGroup):
                continue
            self.goto_group(group)
            insight = False
            for assignment in self._iter_entries():
                if assignment in self.dispatched:
                    continue
                logger.hr('Assignment event', level=2)
                logger.info(f'Check assignment event: {assignment}')
                # Order of entries changes if claimed
                self.goto_entry(assignment, insight=insight)
                insight = False
                status = self._check_assignment_status()
                if status == AssignmentStatus.LOCKED:
                    continue
                elif status == AssignmentStatus.CLAIMABLE:
                    self.claim(assignment, None, should_redispatch=False)
                    claimed = True
                    insight = True
                elif status == AssignmentStatus.DISPATCHABLE:
                    self.dispatch(assignment, None)
                elif status == AssignmentStatus.DISPATCHED:
                    self.dispatched[assignment] = datetime.now() + \
                        self._get_assignment_time()
        return claimed

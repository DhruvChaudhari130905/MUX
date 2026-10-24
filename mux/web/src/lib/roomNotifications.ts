// Turns live room events into notifications (see notifications.ts)
import type { AppEvent, ChangeRequest, PlanItem, User } from '@/types';
import { notify } from './notifications';
import { isMentioned } from './mentions';

export interface RoomNotifyContext {
  roomTitle: string;
  currentUser: User;
  nameOf: (userId: string) => string;
  planItem: (id: string) => PlanItem | undefined;
  changeRequest?: (id: string) => ChangeRequest | undefined;
  focusSidePanel?: () => void;
}

const clip = (text: string, n = 140) => (text.length > n ? `${text.slice(0, n - 1)}…` : text);

export function notifyForEvent(event: AppEvent, ctx: RoomNotifyContext) {
  const { currentUser, roomTitle } = ctx;

  switch (event.type) {
    case 'message.posted': {
      const msg = event.payload;
      if (msg.user_id === currentUser.id) return; // your own message
      const who = ctx.nameOf(msg.user_id);
      if (isMentioned(msg.text, currentUser)) {
        notify({ category: 'mentions', title: `${who} mentioned you in ${roomTitle}`, body: clip(msg.text) });
      } else {
        notify({ category: 'messages', title: `${who} · ${roomTitle}`, body: clip(msg.text) });
      }
      return;
    }

    case 'plan.item_updated': {
      const status = event.payload.changes.status;
      if (status !== 'done' && status !== 'skipped_conflict' && status !== 'skipped_question') return;
      const title = event.payload.changes.title ?? ctx.planItem(event.payload.id)?.title ?? 'A task';
      notify(
        status === 'done'
          ? { category: 'tasks', tone: 'ok', title: 'Task completed', body: title }
          : { category: 'tasks', tone: 'err', title: 'Task skipped', body: `${title} · waiting on a ${status === 'skipped_conflict' ? 'vote' : 'answer'}` },
      );
      return;
    }

    case 'build.result': {
      const { passed, duration, errors } = event.payload;
      notify(
        passed
          ? { category: 'builds', tone: 'ok', title: 'Build passed', body: `${roomTitle} · ${duration.toFixed(1)}s` }
          : { category: 'builds', tone: 'err', title: 'Build failed', body: errors[0] ? clip(errors[0]) : `${errors.length} errors` },
      );
      return;
    }

    case 'question.opened':
      notify({ category: 'decisions', title: 'The agent has a question', body: clip(event.payload.text), onClick: ctx.focusSidePanel });
      return;

    case 'conflict.opened':
      notify({
        category: 'decisions',
        title: 'Your vote is needed',
        body: clip(event.payload.options.join(' vs ')),
        onClick: ctx.focusSidePanel,
      });
      return;

    case 'change.requested': {
      const req = event.payload;
      if (req.requested_by === currentUser.id || !req.approvers.includes(currentUser.id)) return;
      notify({
        category: 'decisions',
        title: `${ctx.nameOf(req.requested_by)} needs your approval`,
        body: `${req.via === 'agent' ? 'The agent wants to' : 'Wants to'} ${req.content === null ? 'delete' : 'change'} ${req.path}`,
        onClick: ctx.focusSidePanel,
      });
      return;
    }

    case 'change.decided': {
      const req = ctx.changeRequest?.(event.payload.id);
      const { status, decided_by } = event.payload;
      if (!req || req.requested_by !== currentUser.id || status === 'withdrawn') return;
      notify(
        status === 'approved'
          ? { category: 'decisions', tone: 'ok', title: 'Change approved', body: `${ctx.nameOf(decided_by)} approved ${req.path}` }
          : status === 'stale'
            ? { category: 'decisions', tone: 'err', title: 'Change not applied', body: `${req.path} changed before it was approved. Redo your change.` }
            : { category: 'decisions', tone: 'err', title: 'Change rejected', body: `${ctx.nameOf(decided_by)} rejected ${req.path}` },
      );
      return;
    }

    case 'conflict.closed':
      notify({ category: 'decisions', tone: 'ok', title: 'Vote closed', body: clip(event.payload.result) });
      return;

    default:
      return;
  }
}

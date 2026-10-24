// WebSocket client for real-time events and presence
import type { AppEvent, Presence, RoomState } from '@/types';
import { demoRoomEvents, isDemoMode } from './demo';
import { applyEvent, createEmptyState } from './reducer';
import { getAccessToken } from './supabase';

type EventHandler = (event: AppEvent) => void;
type PresenceHandler = (presence: Presence[]) => void;
type StateHandler = (state: RoomState) => void;

// Presence updates carry the room's current seq rather than a new one, so they never count as replays
const EPHEMERAL_TYPES = new Set(['presence.tab', 'presence.typing', 'presence.snapshot']);

export class SocketClient {
  private ws: WebSocket | null = null;
  private apiHost: string;
  private roomId: string;
  private since: number;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private reconnectDelay = 1000;
  private eventHandlers: Set<EventHandler> = new Set();
  private presenceHandlers: Set<PresenceHandler> = new Set();
  private stateHandlers: Set<StateHandler> = new Set();
  private pendingEvents: AppEvent[] = [];
  private currentState: RoomState | null = null;
  private isConnected = false;
  // The connection being opened, so a second connect() (React runs effects twice in development) reuses it
  private connecting: Promise<void> | null = null;
  // Set by disconnect(): a socket closed on purpose must not reconnect
  private closedByClient = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(roomId: string, since: number = 0) {
    this.roomId = roomId;
    this.since = since;
    this.apiHost = process.env.NEXT_PUBLIC_API_URL?.replace(/^http/, 'ws') || 'ws://localhost:8000';
  }

  connect(): Promise<void> {
    this.closedByClient = false;
    if (this.ws && this.ws.readyState === WebSocket.OPEN) return Promise.resolve();
    if (!this.connecting) {
      this.connecting = this.open().finally(() => { this.connecting = null; });
    }
    return this.connecting;
  }

  private async open(): Promise<void> {
    if (isDemoMode()) {
      // Replay a canned room instead of opening a WebSocket
      if (!this.currentState) this.handleMessage(demoRoomEvents(this.roomId));
      this.isConnected = true;
      return;
    }
    const token = await getAccessToken();
    // Resume from the last seq seen, so a reconnect replays only what was missed
    const url = `${this.apiHost}/rooms/${this.roomId}/ws?since=${this.since}`;
    if (this.closedByClient) return;
    return new Promise((resolve, reject) => {
      try {
        const ws = new WebSocket(url);
        this.ws = ws;

        this.ws.onopen = () => {
          console.log('[WS] Connected');
          // The first message authenticates the socket (docs/06-event-catalog.md)
          this.ws?.send(JSON.stringify({ type: 'auth', payload: { token } }));
          this.isConnected = true;
          this.reconnectAttempts = 0;
          this.flushPendingEvents();
          resolve();
        };

        this.ws.onmessage = (event) => {
          if (this.ws !== ws) return;
          try {
            const data = JSON.parse(event.data);
            this.handleMessage(data);
          } catch (e) {
            console.error('[WS] Failed to parse message:', e);
          }
        };

        this.ws.onclose = (event) => {
          if (this.ws !== ws) return; // a replaced socket
          console.log('[WS] Disconnected:', event.code, event.reason);
          this.isConnected = false;
          if (!this.closedByClient) this.scheduleReconnect();
        };

        this.ws.onerror = (error) => {
          console.error('[WS] Error:', error);
          if (!this.isConnected) {
            reject(new Error('WebSocket connection failed'));
          }
        };
      } catch (e) {
        reject(e);
      }
    });
  }

  private handleMessage(data: unknown) {
    // Handle initial state dump (array of events)
    if (Array.isArray(data)) {
      for (const event of data) {
        this.applyEvent(event as AppEvent);
      }
      this.notifyState();
      return;
    }

    // Handle single event
    const event = data as AppEvent;
    this.applyEvent(event);
    this.notifyEvent(event);
    this.notifyState();
  }

  private applyEvent(event: AppEvent) {
    // Each event has a unique seq; one already applied (overlapping dump, resend) is skipped
    if (!EPHEMERAL_TYPES.has(event.type)) {
      // Live budget.updated messages aren't stored and carry the current seq; each is a full snapshot, so applying it again is safe
      const liveBudget = event.type === 'budget.updated' && event.seq === this.since;
      if (event.seq <= this.since && this.currentState && !liveBudget) return;
      this.since = Math.max(this.since, event.seq);
    }
    // One reducer for every event type (src/lib/reducer.ts), so the live room and replay agree
    const state = this.currentState ?? createEmptyState();
    if (!state.room.id) state.room = { ...state.room, id: this.roomId };
    this.currentState = applyEvent(state, event);
  }

  private flushPendingEvents() {
    // Events that arrived before connection
  }

  private notifyEvent(event: AppEvent) {
    this.eventHandlers.forEach(h => h(event));
  }

  private notifyState() {
    if (this.currentState) {
      // State is mutated in place, so hand out a fresh snapshot or React won't re-render
      const s = this.currentState;
      const snapshot: RoomState = {
        ...s,
        plan: s.plan.map(p => ({ ...p })),
        messages: [...s.messages],
        conflicts: s.conflicts.map(c => ({ ...c, votes: [...c.votes] })),
        questions: s.questions.map(q => ({ ...q })),
        files: new Map(s.files),
        checkpoints: [...s.checkpoints],
        presence: s.presence.map(p => ({ ...p })),
      };
      this.stateHandlers.forEach(h => h(snapshot));
      // Also compute presence for convenience
      this.presenceHandlers.forEach(h => h(this.currentState!.presence));
    }
  }

  private scheduleReconnect() {
    if (this.reconnectTimer) return;
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error('[WS] Max reconnect attempts reached');
      return;
    }
    const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts);
    this.reconnectAttempts++;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (!this.closedByClient) void this.connect().catch(() => this.scheduleReconnect());
    }, delay);
  }

  // Apply an event produced locally (demo mode) as if the server had sent it
  injectEvent(event: AppEvent) {
    this.handleMessage(event);
  }

  onEvent(handler: EventHandler) {
    this.eventHandlers.add(handler);
    return () => this.eventHandlers.delete(handler);
  }

  onPresence(handler: PresenceHandler) {
    this.presenceHandlers.add(handler);
    return () => this.presenceHandlers.delete(handler);
  }

  onState(handler: StateHandler) {
    this.stateHandlers.add(handler);
    if (this.currentState) handler(this.currentState);
    return () => this.stateHandlers.delete(handler);
  }

  send(event: { type: string; payload: unknown }) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(event));
    }
  }

  sendPresence(tab: Presence['tab']) {
    this.send({ type: 'presence.tab', payload: { tab } });
  }

  sendTyping(typing: boolean) {
    this.send({ type: 'presence.typing', payload: { typing } });
  }

  disconnect() {
    this.closedByClient = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    this.ws?.close(1000, 'Client disconnect');
    this.ws = null;
    this.isConnected = false;
  }

  getState(): RoomState | null {
    return this.currentState;
  }

  getSince(): number {
    return this.since;
  }
}

// Singleton for the current room
let currentSocket: SocketClient | null = null;

export function getSocket(roomId: string, since?: number): SocketClient {
  if (currentSocket && currentSocket['roomId'] === roomId) {
    return currentSocket;
  }
  if (currentSocket) {
    currentSocket.disconnect();
  }
  currentSocket = new SocketClient(roomId, since);
  return currentSocket;
}

export function clearSocket() {
  if (currentSocket) {
    currentSocket.disconnect();
    currentSocket = null;
  }
}

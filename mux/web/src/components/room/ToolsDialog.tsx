'use client';

import React, { useEffect, useState } from 'react';
import { X, Plug, RefreshCw, Trash2 } from 'lucide-react';
import type { McpTool, McpToolSetting, RoomMcp, RoomSkill } from '@/types';
import { api } from '@/lib/api';
import { useEscapeClose } from '@/lib/useEscapeClose';
interface ToolsDialogProps {
  isOpen: boolean;
  onClose: () => void;
  roomId: string;
  isOwner: boolean;
}

const field = 'bg-[var(--bg)] border border-[var(--line)] rounded px-3 py-2 text-sm';
const section = 'space-y-3 mb-4 p-4 bg-[var(--raised)] rounded-lg';

function settingOf(settings: Record<string, McpToolSetting>, tool: string): McpToolSetting {
  return { enabled: settings[tool]?.enabled ?? true, mode: settings[tool]?.mode === 'ask' ? 'ask' : 'auto' };
}

function ToolRows({ tools, settings, canEdit, onChange }: {
  tools: McpTool[];
  settings: Record<string, McpToolSetting>;
  canEdit: boolean;
  onChange: (tool: string, setting: McpToolSetting) => void;
}) {
  if (!tools.length) return <p className="text-sm text-[var(--muted)]">No tools listed yet.</p>;
  return (
    <div className="space-y-1">
      {tools.map(tool => {
        const s = settingOf(settings, tool.name);
        return (
          <div key={tool.name} className="flex items-center justify-between gap-2 text-sm">
            <label className="flex min-w-0 items-center gap-2" title={tool.description}>
              <input type="checkbox" checked={s.enabled} disabled={!canEdit}
                onChange={e => onChange(tool.name, { ...s, enabled: e.target.checked })} />
              <span className="truncate font-mono">{tool.name}</span>
            </label>
            <select className={`${field} py-1`} value={s.mode} disabled={!canEdit || !s.enabled}
              onChange={e => onChange(tool.name, { ...s, mode: e.target.value as 'auto' | 'ask' })}
              aria-label={`When the coder uses ${tool.name}`}>
              <option value="auto">Auto</option>
              <option value="ask">Ask first</option>
            </select>
          </div>
        );
      })}
    </div>
  );
}

export function ToolsDialog({ isOpen, onClose, roomId, isOwner }: ToolsDialogProps) {
  const [mcp, setMcp] = useState<RoomMcp | null>(null);
  const [skills, setSkills] = useState<RoomSkill[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [url, setUrl] = useState('');
  const [headerName, setHeaderName] = useState('Authorization');
  const [headerValue, setHeaderValue] = useState('');

  useEffect(() => {
    if (!isOpen) return;
    setError(null);
    api.getMcp(roomId).then(setMcp).catch(e => setError(e instanceof Error ? e.message : 'Could not load MCP servers'));
    api.getSkills(roomId).then(setSkills).catch(e => setError(e instanceof Error ? e.message : 'Could not load skills'));
  }, [isOpen, roomId]);

  useEscapeClose(isOpen, onClose);

  if (!isOpen) return null;

  const run = async (action: () => Promise<RoomMcp>) => {
    setBusy(true);
    setError(null);
    try {
      setMcp(await action());
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong');
      return false;
    } finally {
      setBusy(false);
    }
  };

  const runSkills = async (action: () => Promise<RoomSkill[]>) => {
    setBusy(true);
    setError(null);
    try {
      setSkills(await action());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong');
    } finally {
      setBusy(false);
    }
  };

  const toggleSkill = (name: string, on: boolean) => {
    const enabled = (skills ?? []).filter(s => s.enabled && s.name !== name).map(s => s.name);
    return runSkills(() => api.setSkills(roomId, on ? [...enabled, name] : enabled));
  };

  const addServer = async (e: React.FormEvent) => {
    e.preventDefault();
    const headers = headerValue ? { [headerName.trim() || 'Authorization']: headerValue } : {};
    if (await run(() => api.addMcpServer(roomId, { name: name.trim(), url: url.trim(), headers }))) {
      setName('');
      setUrl('');
      setHeaderValue('');
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center glass-overlay p-4" onClick={onClose}>
      <div className="glass-modal rounded-2xl p-6 w-full max-w-lg max-h-[90vh] overflow-y-auto"
        onClick={e => e.stopPropagation()} role="dialog" aria-modal="true" aria-labelledby="tools-title">
        <div className="flex items-center justify-between mb-2">
          <h2 id="tools-title" className="text-lg font-semibold flex items-center gap-2"><Plug className="w-5 h-5" /> Tools and skills</h2>
          <button className="btn p-2" onClick={onClose} type="button" aria-label="Close"><X className="w-5 h-5" /></button>
        </div>
        <p className="mb-4 text-sm text-[var(--muted)]">
          Tools from these servers are offered to the coder. &ldquo;Ask first&rdquo; tools wait for someone in the room to allow each use.
          {!isOwner && ' Only the owner can change them.'}
        </p>
        {error && <p className="mb-3 text-sm text-[var(--conflict)]" role="alert">{error}</p>}
        {skills && (
          <div className={section}>
            <div className="flex items-center justify-between gap-2">
              <p className="font-medium">Skills</p>
              {isOwner && (
                <button type="button" className="btn p-1" disabled={busy} title="Re-read the server's skills folder" aria-label="Reload skills"
                  onClick={() => runSkills(() => api.reloadSkills(roomId))}><RefreshCw className="w-3.5 h-3.5" /></button>
              )}
            </div>
            {skills.length === 0 && <p className="text-sm text-[var(--muted)]">No skills on the server yet. Put skill folders (each with a SKILL.md) in the server&apos;s skills folder.</p>}
            {skills.map(skill => (
              <label key={skill.name} className="flex items-start gap-2 text-sm" title={skill.description}>
                <input type="checkbox" className="mt-1" checked={skill.enabled} disabled={!isOwner || busy}
                  onChange={e => toggleSkill(skill.name, e.target.checked)} />
                <span className="min-w-0">
                  <span className="font-mono">{skill.name}</span>
                  {skill.missing && <span className="ml-2 text-xs text-[var(--conflict)]">missing on the server</span>}
                  {!skill.compatible && (
                    <span className="ml-2 text-xs text-[var(--conflict)]" title={skill.issues.join('; ')}>needs Claude Code</span>
                  )}
                  {skill.description && <span className="block truncate text-xs text-[var(--muted)]">{skill.description}</span>}
                </span>
              </label>
            ))}
          </div>
        )}
        {!mcp ? (
          !error && <p className="text-sm text-[var(--muted)]">Loading…</p>
        ) : (
          <>
            {mcp.admin.length > 0 && (
              <div className={section}>
                <p className="font-medium">From the MUX server</p>
                {mcp.admin.map(server => (
                  <div key={server.name} className="space-y-2 border-t border-[var(--line)] pt-2 first:border-0 first:pt-0">
                    <div className="flex items-center justify-between gap-2">
                      <label className="flex items-center gap-2 text-sm">
                        <input type="checkbox" checked={server.enabled} disabled={!isOwner || busy}
                          onChange={e => run(() => api.updateMcpAdmin(roomId, server.name, { enabled: e.target.checked }))} />
                        <span className="font-medium">{server.name}</span>
                        <span className="text-[var(--muted)]">{server.kind === 'stdio' ? 'local' : 'remote'}</span>
                      </label>
                      {isOwner && (
                        <button type="button" className="btn p-1" disabled={busy} title="Load its tools" aria-label={`Load ${server.name} tools`}
                          onClick={() => run(() => api.refreshMcpAdmin(roomId, server.name))}>
                          <RefreshCw className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </div>
                    {server.tools_loaded
                      ? <ToolRows tools={server.tools} settings={server.settings} canEdit={isOwner && !busy}
                          onChange={(tool, s) => run(() => api.updateMcpAdmin(roomId, server.name,
                            { enabled: server.enabled, settings: { ...server.settings, [tool]: s } }))} />
                      : <p className="text-sm text-[var(--muted)]">Load its tools to set them one by one.</p>}
                  </div>
                ))}
              </div>
            )}

            <div className={section}>
              <p className="font-medium">This room&apos;s servers</p>
              {mcp.servers.length === 0 && <p className="text-sm text-[var(--muted)]">None yet.</p>}
              {mcp.servers.map(server => (
                <div key={server.name} className="space-y-2 border-t border-[var(--line)] pt-2 first:border-0 first:pt-0">
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <p className="text-sm font-medium">{server.name}</p>
                      <p className="truncate font-mono text-xs text-[var(--muted)]">{server.url}
                        {server.header_names.length > 0 && ` · ${server.header_names.join(', ')} set`}</p>
                    </div>
                    {isOwner && (
                      <span className="flex gap-1">
                        <button type="button" className="btn p-1" disabled={busy} title="Reload its tools" aria-label={`Reload ${server.name}`}
                          onClick={() => run(() => api.refreshMcpServer(roomId, server.name))}><RefreshCw className="w-3.5 h-3.5" /></button>
                        <button type="button" className="btn p-1" disabled={busy} title="Remove" aria-label={`Remove ${server.name}`}
                          onClick={() => run(() => api.removeMcpServer(roomId, server.name))}><Trash2 className="w-3.5 h-3.5" /></button>
                      </span>
                    )}
                  </div>
                  <ToolRows tools={server.tools} settings={server.settings} canEdit={isOwner && !busy}
                    onChange={(tool, s) => run(() => api.updateMcpServer(roomId, server.name, { settings: { ...server.settings, [tool]: s } }))} />
                </div>
              ))}
            </div>

            {isOwner && (
              <form onSubmit={addServer} className={section}>
                <p className="font-medium">Add a server</p>
                <div className="flex gap-2">
                  <input className={`w-32 ${field}`} value={name} onChange={e => setName(e.target.value.toLowerCase())}
                    placeholder="name" pattern="[a-z0-9_\-]{1,32}" required aria-label="Server name" />
                  <input className={`flex-1 min-w-0 font-mono ${field}`} value={url} onChange={e => setUrl(e.target.value)}
                    placeholder="https://example.com/mcp" type="url" required aria-label="Server URL" />
                </div>
                <div className="flex gap-2">
                  <input className={`w-32 ${field}`} value={headerName} onChange={e => setHeaderName(e.target.value)} aria-label="Header name" />
                  <input className={`flex-1 min-w-0 ${field}`} value={headerValue} onChange={e => setHeaderValue(e.target.value)}
                    type="password" autoComplete="off" placeholder="Token (optional), e.g. Bearer …" aria-label="Header value" />
                </div>
                <p className="text-xs text-[var(--muted)]">Public https servers only. The token is stored encrypted and never shown again.</p>
                <button type="submit" className="btn primary" disabled={busy || !name || !url}>{busy ? 'Connecting…' : 'Add server'}</button>
              </form>
            )}
          </>
        )}
        <div className="flex justify-end"><button className="btn" onClick={onClose} type="button">Done</button></div>
      </div>
    </div>
  );
}

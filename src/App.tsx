/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  Shield,
  Smartphone,
  ScrollText,
  Users,
  Columns2,
  Plus,
  Menu,
  X,
  Radio,
  Send,
  HelpCircle,
  AlertOctagon,
  Activity,
  Layers,
} from 'lucide-react';
import type {
  ApprovalRecord,
  AuditChainVerification,
  AuditEvent,
  BackendMode,
  BreakGlassRequest,
  Employee,
  EmployeeRole,
  FixtureScenario,
  MockExecutionReceipt,
} from './types';
import { voiceGateEngine } from './lib/voiceGateEngine';
import { RequesterConsole } from './components/RequesterConsole';
import { ApproverPhone } from './components/ApproverPhone';
import { AuditDashboard } from './components/AuditDashboard';
import { EnrollmentPanel } from './components/EnrollmentPanel';
import { LiveGuardPanel } from './components/LiveGuardPanel';
import { TelegramPreview } from './components/TelegramPreview';
import { AmbientBackground } from './components/ui/AmbientBackground';
import { PressableButton } from './components/ui/PressableButton';
import { StatusBadge } from './components/ui/StatusBadge';

export type ActiveTab = 'requester' | 'live_guard' | 'approver' | 'audit' | 'enrollment' | 'telegram';

export const NAV_ITEMS: { id: ActiveTab; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { id: 'requester', label: 'Requester Gate', icon: Shield },
  { id: 'live_guard', label: 'Live Guard', icon: Radio },
  { id: 'approver', label: 'Approver Phone', icon: Smartphone },
  { id: 'audit', label: 'Operations & Audit', icon: ScrollText },
  { id: 'enrollment', label: 'Voice Enrollment', icon: Users },
  { id: 'telegram', label: 'Telegram Channel', icon: Send },
];

export const SCENARIOS: { id: FixtureScenario; label: string }[] = [
  { id: 'pass', label: 'Pass' },
  { id: 'borderline', label: 'Borderline' },
  { id: 'fail', label: 'Fail' },
  { id: 'error', label: 'Error' },
];

export default function App() {
  const [activeTab, setActiveTab] = useState<ActiveTab>('requester');
  const [splitPhoneView, setSplitPhoneView] = useState(true);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const [backendMode, setBackendMode] = useState<BackendMode>('local');
  const [apiBaseUrl, setApiBaseUrl] = useState(voiceGateEngine.getApiBaseUrl());
  const [scenario, setScenario] = useState<FixtureScenario>('pass');
  const [liveHealthy, setLiveHealthy] = useState<boolean | null>(null);

  const [showHowItWorks, setShowHowItWorks] = useState(false);
  const [showLimitations, setShowLimitations] = useState(false);

  const [employees, setEmployees] = useState<Employee[]>([]);
  const [requests, setRequests] = useState<BreakGlassRequest[]>([]);
  const [activeRequestId, setActiveRequestId] = useState<string | null>(null);
  const [pendingApprovals, setPendingApprovals] = useState<ApprovalRecord[]>([]);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [chainVerification, setChainVerification] = useState<AuditChainVerification | null>(null);

  const [busy, setBusy] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const syncAll = useCallback(async () => {
    try {
      const [emps, reqs, pending, events] = await Promise.all([
        voiceGateEngine.listEmployees(),
        voiceGateEngine.listRequests(),
        voiceGateEngine.listPendingApprovals(),
        voiceGateEngine.listAuditEvents(),
      ]);
      setEmployees(emps);
      setRequests(reqs);
      setPendingApprovals(pending);
      setAuditEvents(events);
      setLiveHealthy(backendMode === 'live' ? true : null);
    } catch (err) {
      if (backendMode === 'live') {
        setLiveHealthy(false);
      }
      setErrorMessage(
        err instanceof Error
          ? err.message
          : 'Unable to reach backend API. Verify FastAPI is running on port 8000 or switch to Local Demo Engine.',
      );
    }
  }, [backendMode]);

  useEffect(() => {
    syncAll();
  }, [syncAll]);

  // Poll pending approvals & expiry updates gently when in live FastAPI mode
  useEffect(() => {
    if (backendMode !== 'live') return;
    const id = window.setInterval(() => {
      syncAll();
    }, 5000);
    return () => window.clearInterval(id);
  }, [backendMode, syncAll]);

  const handleModeSwitch = async (nextMode: BackendMode) => {
    setErrorMessage(null);
    voiceGateEngine.setMode(nextMode, apiBaseUrl);
    setBackendMode(nextMode);
    setActiveRequestId(null);
    setChainVerification(null);
    if (nextMode === 'live') {
      const ok = await voiceGateEngine.checkHealth();
      setLiveHealthy(ok);
      if (!ok) {
        setErrorMessage(
          `Could not reach FastAPI at ${apiBaseUrl}. Start it with: uvicorn backend.app.main:app --port 8000`,
        );
        return;
      }
    } else {
      setLiveHealthy(null);
    }
    await syncAll();
  };

  const handleScenarioChange = (next: FixtureScenario) => {
    setScenario(next);
    voiceGateEngine.setScenario(next);
  };

  const handleCreateRequest = async (input: {
    employee_id: string;
    action_summary: string;
    target_resource: string;
    justification: string;
  }) => {
    setBusy(true);
    setErrorMessage(null);
    try {
      const created = await voiceGateEngine.createRequest(input);
      setActiveRequestId(created.request_id);
      await syncAll();
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : 'Failed to create break-glass request.');
    } finally {
      setBusy(false);
    }
  };

  const handleRefreshChallenge = async (requestId: string) => {
    setBusy(true);
    setErrorMessage(null);
    try {
      await voiceGateEngine.refreshChallenge(requestId);
      await syncAll();
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : 'Could not refresh challenge phrase.');
    } finally {
      setBusy(false);
    }
  };

  const handleVerifyVoice = async (requestId: string, file: File, durationSeconds: number) => {
    setBusy(true);
    setErrorMessage(null);
    try {
      await voiceGateEngine.verifyVoice(requestId, file, durationSeconds);
      await syncAll();
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : 'Voice verification failed.');
    } finally {
      setBusy(false);
    }
  };

  const handleReissueApproval = async (requestId: string) => {
    setBusy(true);
    setErrorMessage(null);
    try {
      await voiceGateEngine.reissueApproval(requestId);
      await syncAll();
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : 'Could not reissue approval code.');
    } finally {
      setBusy(false);
    }
  };

  const handleDecideApproval = async (
    approvalId: string,
    payload: {
      reviewer_id: string;
      action: 'approve' | 'reject';
      matching_number?: string;
      notes: string;
      peer_review_confirmed: boolean;
    },
  ) => {
    setBusy(true);
    setErrorMessage(null);
    try {
      const updated = await voiceGateEngine.decideApproval(approvalId, payload);
      setActiveRequestId(updated.request_id);
      await syncAll();
    } finally {
      setBusy(false);
    }
  };

  const handleExecuteRequest = async (
    requestId: string,
    actorId: string,
  ): Promise<MockExecutionReceipt | null> => {
    setBusy(true);
    setErrorMessage(null);
    try {
      const receipt = await voiceGateEngine.executeRequest(requestId, actorId);
      await syncAll();
      return receipt;
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : 'Simulated execution blocked.');
      return null;
    } finally {
      setBusy(false);
    }
  };

  const handleVerifyChain = async () => {
    const result = await voiceGateEngine.verifyAuditChain();
    setChainVerification(result);
  };

  const handleSimulateTamper = async () => {
    voiceGateEngine.simulateLocalAuditTamper();
    await syncAll();
    const result = await voiceGateEngine.verifyAuditChain();
    setChainVerification(result);
  };

  const handleCreateEmployee = async (input: {
    employee_id: string;
    display_name: string;
    role: EmployeeRole;
    known_callback_hint: string;
  }) => {
    setBusy(true);
    try {
      await voiceGateEngine.createEmployee(input);
      await syncAll();
    } finally {
      setBusy(false);
    }
  };

  const handleEnrollSample = async (employeeId: string, file: File): Promise<string> => {
    setBusy(true);
    try {
      const res = await voiceGateEngine.enrollEmployeeSample(employeeId, file);
      await syncAll();
      return res.message;
    } finally {
      setBusy(false);
    }
  };

  const activeRequest = requests.find((r) => r.request_id === activeRequestId) || null;
  const activeTabLabel = NAV_ITEMS.find((n) => n.id === activeTab)?.label || 'Requester Gate';

  const SidebarContent = (
    <div className="flex flex-col h-full bg-slate-950/80 backdrop-blur-2xl border-r border-cyan-500/10">
      {/* Brand Header */}
      <div className="h-20 px-6 flex items-center justify-between border-b border-white/[0.06]">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-cyan-500 to-indigo-600 flex items-center justify-center shadow-[0_0_20px_rgba(6,182,212,0.4)] ring-1 ring-cyan-300/40">
            <Shield className="w-5 h-5 text-white" />
          </div>
          <div>
            <span className="font-display text-base font-bold tracking-tight text-white block">
              Break-Glass
            </span>
            <span className="text-[11px] font-mono tracking-wider text-cyan-400 block -mt-0.5">
              VOICE GATE · 2K26
            </span>
          </div>
        </div>
      </div>

      {/* Nav Menu */}
      <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          const active = activeTab === item.id;
          return (
            <button
              key={item.id}
              data-testid={`nav-tab-${item.id}`}
              type="button"
              onClick={() => {
                setActiveTab(item.id);
                setMobileMenuOpen(false);
              }}
              className={`w-full flex items-center justify-between gap-3 px-3.5 py-3 text-sm font-medium rounded-xl transition-all duration-200 cursor-pointer ${
                active
                  ? 'bg-gradient-to-r from-cyan-500/20 to-indigo-500/10 text-cyan-300 border border-cyan-500/30 shadow-[0_0_15px_rgba(6,182,212,0.15)]'
                  : 'text-slate-400 hover:text-white hover:bg-white/[0.04]'
              }`}
            >
              <span className="inline-flex items-center gap-3">
                <Icon className={`w-4 h-4 shrink-0 ${active ? 'text-cyan-400' : 'text-slate-500'}`} />
                {item.label}
              </span>
              {item.id === 'approver' && pendingApprovals.length > 0 && (
                <span className="font-mono text-xs px-2 py-0.5 rounded-full bg-cyan-500/20 text-cyan-300 border border-cyan-500/30 font-semibold tabular-nums">
                  {pendingApprovals.length}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      {/* Sidebar Engine & Controls */}
      <div className="p-4 border-t border-white/[0.06] space-y-4 bg-slate-950/60">
        <div className="space-y-1.5">
          <div className="text-[11px] font-medium text-slate-400 uppercase tracking-wider font-mono">
            Backend Engine
          </div>
          <div className="grid grid-cols-2 gap-1 p-1 bg-slate-900/90 rounded-lg border border-white/5">
            <button
              type="button"
              data-testid="toggle-mode-local"
              onClick={() => handleModeSwitch('local')}
              className={`py-1.5 text-xs font-medium rounded-md transition-all cursor-pointer ${
                backendMode === 'local'
                  ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/30 shadow-xs'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              Demo Engine
            </button>
            <button
              type="button"
              data-testid="toggle-mode-live"
              onClick={() => handleModeSwitch('live')}
              className={`py-1.5 text-xs font-medium rounded-md transition-all cursor-pointer ${
                backendMode === 'live'
                  ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/30 shadow-xs'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              Live FastAPI
            </button>
          </div>
        </div>

        {backendMode === 'local' ? (
          <div className="space-y-1.5">
            <div className="text-[11px] font-medium text-slate-400 uppercase tracking-wider font-mono">
              Voice Fixture Outcome
            </div>
            <div className="grid grid-cols-2 gap-1 p-1 bg-slate-900/90 rounded-lg border border-white/5">
              {SCENARIOS.map((sc) => (
                <button
                  key={sc.id}
                  type="button"
                  data-testid={`scenario-btn-${sc.id}`}
                  onClick={() => handleScenarioChange(sc.id)}
                  className={`py-1 text-xs font-medium rounded-md transition-all cursor-pointer ${
                    scenario === sc.id
                      ? 'bg-white/10 text-white font-semibold'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {sc.label}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="space-y-1.5">
            <label htmlFor="api-base-url" className="block text-[11px] font-medium text-slate-400">
              FastAPI URL
            </label>
            <input
              id="api-base-url"
              type="url"
              value={apiBaseUrl}
              onChange={(e) => {
                setApiBaseUrl(e.target.value);
                voiceGateEngine.setMode('live', e.target.value);
              }}
              onBlur={() => handleModeSwitch('live')}
              className="w-full h-8 px-2.5 text-xs font-mono bg-slate-900/80 border border-white/10 rounded-md text-white focus:outline-none focus:border-cyan-400"
            />
            {liveHealthy !== null && (
              <div
                className={`text-[11px] font-medium flex items-center gap-1.5 ${
                  liveHealthy ? 'text-emerald-400' : 'text-rose-400'
                }`}
              >
                <span className={`w-1.5 h-1.5 rounded-full ${liveHealthy ? 'bg-emerald-400 shadow-[0_0_6px_#34d399]' : 'bg-rose-400 shadow-[0_0_6px_#f43f5e]'}`} />
                {liveHealthy ? 'Connected to FastAPI' : 'Backend unreachable'}
              </div>
            )}
          </div>
        )}

        {/* Quick Modal Links */}
        <div className="pt-2 flex items-center justify-between text-xs text-slate-400 border-t border-white/[0.04]">
          <button
            type="button"
            onClick={() => setShowHowItWorks(true)}
            className="hover:text-cyan-400 inline-flex items-center gap-1 transition-colors cursor-pointer"
          >
            <HelpCircle className="w-3.5 h-3.5" />
            How it works
          </button>
          <button
            type="button"
            onClick={() => setShowLimitations(true)}
            className="hover:text-amber-400 inline-flex items-center gap-1 transition-colors cursor-pointer"
          >
            <AlertOctagon className="w-3.5 h-3.5" />
            Limitations
          </button>
        </div>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen flex bg-[#030712] text-slate-100 font-sans antialiased relative selection:bg-cyan-500/30 selection:text-cyan-200">
      <AmbientBackground />

      {/* Desktop Sidebar */}
      <aside className="hidden lg:block w-[268px] shrink-0 sticky top-0 h-screen z-40">
        {SidebarContent}
      </aside>

      {/* Mobile Drawer */}
      {mobileMenuOpen && (
        <div className="fixed inset-0 z-50 lg:hidden flex">
          <div className="w-[280px] h-full relative">
            <button
              type="button"
              aria-label="Close navigation"
              onClick={() => setMobileMenuOpen(false)}
              className="absolute top-4 right-4 p-1.5 text-slate-400 hover:text-white cursor-pointer z-50"
            >
              <X className="w-5 h-5" />
            </button>
            {SidebarContent}
          </div>
          <div
            className="flex-1 bg-black/60 backdrop-blur-sm"
            onClick={() => setMobileMenuOpen(false)}
          />
        </div>
      )}

      {/* Main Workspace */}
      <div className="flex-1 min-w-0 flex flex-col">
        {/* Floating Glass Top Bar */}
        <header className="h-16 px-4 sm:px-6 flex items-center justify-between gap-4 sticky top-0 z-30 bg-slate-950/70 backdrop-blur-xl border-b border-white/[0.06]">
          <div className="flex items-center gap-3 min-w-0">
            <button
              type="button"
              aria-label="Open navigation"
              onClick={() => setMobileMenuOpen(true)}
              className="lg:hidden p-2 -ml-2 text-slate-400 hover:text-white cursor-pointer"
            >
              <Menu className="w-5 h-5" />
            </button>
            <div className="flex items-center gap-2.5 text-sm min-w-0">
              <span className="text-slate-400 whitespace-nowrap hidden sm:inline font-medium">
                Break-Glass
              </span>
              <span aria-hidden="true" className="text-slate-600 hidden sm:inline">
                /
              </span>
              <span className="font-semibold text-white truncate font-display tracking-tight">
                {activeTabLabel}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-3 shrink-0">
            {/* Live Health Indicator Dot */}
            <div
              data-testid="health-indicator"
              className="hidden md:inline-flex items-center gap-2 px-3 py-1 rounded-full bg-slate-900/80 border border-white/10 text-xs font-mono"
            >
              <span
                className={`w-2 h-2 rounded-full ${
                  backendMode === 'local'
                    ? 'bg-cyan-400 shadow-[0_0_8px_#06b6d4]'
                    : liveHealthy === true
                      ? 'bg-emerald-400 shadow-[0_0_8px_#10b981]'
                      : liveHealthy === false
                        ? 'bg-rose-400 shadow-[0_0_8px_#f43f5e]'
                        : 'bg-slate-400'
                }`}
              />
              <span className="text-slate-300">
                {backendMode === 'local'
                  ? 'Demo Engine'
                  : liveHealthy === true
                    ? 'FastAPI Connected'
                    : 'Backend Offline'}
              </span>
            </div>

            {/* Simulated Execution Badge */}
            <div
              data-testid="simulated-badge"
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-amber-500/10 border border-amber-500/30 text-amber-300 text-[11px] font-mono tracking-wider font-semibold uppercase"
            >
              <Activity className="w-3 h-3 animate-pulse" />
              <span>Simulated Execution</span>
            </div>

            {activeTab === 'requester' && (
              <button
                type="button"
                data-testid="toggle-split-phone"
                onClick={() => setSplitPhoneView((v) => !v)}
                className={`hidden xl:inline-flex items-center gap-2 px-3.5 py-1.5 text-xs font-medium rounded-lg border transition-all cursor-pointer ${
                  splitPhoneView
                    ? 'bg-cyan-500/15 border-cyan-500/30 text-cyan-300'
                    : 'bg-slate-900/80 border-white/10 text-slate-300 hover:text-white'
                }`}
              >
                <Columns2 className="w-3.5 h-3.5" />
                {splitPhoneView ? 'Hide Phone Split' : 'Show Phone Split'}
              </button>
            )}

            <PressableButton
              variant="primary"
              size="sm"
              icon={Plus}
              data-testid="new-request-btn"
              onClick={() => {
                setActiveRequestId(null);
                setErrorMessage(null);
                setActiveTab('requester');
              }}
            >
              New Request
            </PressableButton>
          </div>
        </header>

        {/* Main Viewport Content */}
        <main className="flex-1 p-4 sm:p-6 lg:p-8 max-w-[1400px] w-full mx-auto">
          {activeTab === 'requester' && (
            <div
              className={
                splitPhoneView
                  ? 'grid grid-cols-1 xl:grid-cols-12 gap-8 items-start'
                  : 'max-w-[920px] mx-auto'
              }
            >
              <div className={splitPhoneView ? 'xl:col-span-8' : ''}>
                <RequesterConsole
                  employees={employees}
                  activeRequest={activeRequest}
                  busy={busy}
                  errorMessage={errorMessage}
                  onCreateRequest={handleCreateRequest}
                  onRefreshChallenge={handleRefreshChallenge}
                  onVerifyVoice={handleVerifyVoice}
                  onReissueApproval={handleReissueApproval}
                  onExecuteRequest={handleExecuteRequest}
                  onOpenApproverPhone={() => {
                    if (window.innerWidth < 1280) {
                      setActiveTab('approver');
                    } else {
                      setSplitPhoneView(true);
                    }
                  }}
                  onStartNew={() => {
                    setActiveRequestId(null);
                    setErrorMessage(null);
                  }}
                />
              </div>

              {splitPhoneView && (
                <div className="xl:col-span-4 xl:sticky xl:top-24">
                  <ApproverPhone
                    employees={employees}
                    pendingApprovals={pendingApprovals}
                    busy={busy}
                    onDecide={handleDecideApproval}
                    compact
                  />
                </div>
              )}
            </div>
          )}

          {activeTab === 'live_guard' && (
            <LiveGuardPanel
              employees={employees}
              requests={requests}
              backendMode={backendMode}
              onSyncAll={syncAll}
              onCreateRequestRedirect={() => {
                setActiveRequestId(null);
                setErrorMessage(null);
                setActiveTab('requester');
              }}
            />
          )}

          {activeTab === 'approver' && (
            <div className="py-2">
              <div className="max-w-[440px] mx-auto mb-6 text-center">
                <StatusBadge status="PENDING" label="Second-Device Gate" className="mb-2" />
                <h1 className="text-2xl font-bold tracking-tight text-white font-display">
                  Second-Device Approver Phone
                </h1>
                <p className="mt-1.5 text-xs text-slate-400 leading-relaxed">
                  Review the exact bound operation and tap the matching 2-digit code before the 2-minute window expires.
                </p>
              </div>
              <ApproverPhone
                employees={employees}
                pendingApprovals={pendingApprovals}
                busy={busy}
                onDecide={handleDecideApproval}
              />
            </div>
          )}

          {activeTab === 'audit' && (
            <AuditDashboard
              requests={requests}
              auditEvents={auditEvents}
              chainVerification={chainVerification}
              backendMode={backendMode}
              onSelectRequest={(req) => {
                setActiveRequestId(req.request_id);
                setActiveTab('requester');
              }}
              onVerifyChain={handleVerifyChain}
              onSimulateTamper={handleSimulateTamper}
            />
          )}

          {activeTab === 'enrollment' && (
            <EnrollmentPanel
              employees={employees}
              busy={busy}
              onCreateEmployee={handleCreateEmployee}
              onEnrollSample={handleEnrollSample}
            />
          )}

          {activeTab === 'telegram' && (
            <TelegramPreview
              activeRequest={activeRequest}
              auditEvents={auditEvents}
            />
          )}
        </main>
      </div>

      {/* How It Works Modal */}
      {showHowItWorks && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-md">
          <div className="relative w-full max-w-2xl bg-slate-950 border border-cyan-500/30 rounded-2xl p-6 sm:p-8 space-y-6 shadow-[0_0_50px_rgba(6,182,212,0.2)]">
            <button
              type="button"
              onClick={() => setShowHowItWorks(false)}
              className="absolute top-5 right-5 text-slate-400 hover:text-white cursor-pointer"
            >
              <X className="w-5 h-5" />
            </button>
            <div className="space-y-2">
              <div className="inline-flex items-center gap-2 text-cyan-400 font-mono text-xs uppercase tracking-wider">
                <Layers className="w-4 h-4" />
                5-Layer Trust Architecture
              </div>
              <h2 className="text-2xl font-bold text-white font-display">How Break-Glass Voice Gate Works</h2>
              <p className="text-xs text-slate-400">
                A convincing voice must never be enough to execute a critical action.
              </p>
            </div>

            <div className="space-y-3 text-xs leading-relaxed text-slate-300">
              <div className="p-3.5 rounded-xl bg-slate-900/80 border border-white/5 space-y-1">
                <div className="font-semibold text-cyan-300 font-mono">Layer 1: Random Spoken Challenge</div>
                <div>Prevents replaying static stolen recordings by binding live audio to fresh phrases.</div>
              </div>
              <div className="p-3.5 rounded-xl bg-slate-900/80 border border-white/5 space-y-1">
                <div className="font-semibold text-emerald-300 font-mono">Layer 2: Aletheia Deepfake Detection</div>
                <div>Inspects spectral anomalies and acoustic synthesis artifacts to detect generative voice cloning.</div>
              </div>
              <div className="p-3.5 rounded-xl bg-slate-900/80 border border-white/5 space-y-1">
                <div className="font-semibold text-blue-300 font-mono">Layer 3: Eidos Speaker Match</div>
                <div>Compares audio embeddings against 1–5 consented enrolled voice samples of the claimed identity.</div>
              </div>
              <div className="p-3.5 rounded-xl bg-slate-900/80 border border-white/5 space-y-1">
                <div className="font-semibold text-amber-300 font-mono">Layer 4: Action-Bound Second-Device Approval</div>
                <div>Number matching with 2-minute expiration, different-person authorization, and out-of-band peer review.</div>
              </div>
              <div className="p-3.5 rounded-xl bg-slate-900/80 border border-white/5 space-y-1">
                <div className="font-semibold text-purple-300 font-mono">Layer 5: Backend Enforcement &amp; Audit Chain</div>
                <div>One-time atomic consumption of approvals, simulated action execution, and SHA-256 chained audit ledger.</div>
              </div>
            </div>

            <PressableButton variant="primary" className="w-full" onClick={() => setShowHowItWorks(false)}>
              Got it
            </PressableButton>
          </div>
        </div>
      )}

      {/* Limitations Modal */}
      {showLimitations && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-md">
          <div className="relative w-full max-w-xl bg-slate-950 border border-amber-500/30 rounded-2xl p-6 sm:p-8 space-y-6 shadow-[0_0_50px_rgba(245,158,11,0.2)]">
            <button
              type="button"
              onClick={() => setShowLimitations(false)}
              className="absolute top-5 right-5 text-slate-400 hover:text-white cursor-pointer"
            >
              <X className="w-5 h-5" />
            </button>
            <div className="space-y-2">
              <div className="inline-flex items-center gap-2 text-amber-400 font-mono text-xs uppercase tracking-wider">
                <AlertOctagon className="w-4 h-4" />
                Honest Security Disclosures
              </div>
              <h2 className="text-2xl font-bold text-white font-display">System Limitations</h2>
            </div>

            <div className="space-y-3 text-xs leading-relaxed text-slate-300">
              <div className="p-3.5 rounded-xl bg-amber-950/20 border border-amber-500/20 space-y-1">
                <span className="font-semibold text-amber-300">Scores are evidence, not proof:</span>
                <p>AI voice-model outputs represent statistical likelihoods. High certainty does not guarantee authenticity.</p>
              </div>
              <div className="p-3.5 rounded-xl bg-amber-950/20 border border-amber-500/20 space-y-1">
                <span className="font-semibold text-amber-300">Challenge phrases don&apos;t halt real-time zero-shot clones:</span>
                <p>Advanced low-latency voice morphers can stream prompted words in near real time; hence second-device approval is mandatory.</p>
              </div>
              <div className="p-3.5 rounded-xl bg-amber-950/20 border border-amber-500/20 space-y-1">
                <span className="font-semibold text-amber-300">Second device is not phishing-resistant FIDO2:</span>
                <p>While number-matching reduces push fatigue, it does not replace cryptographic hardware-token MFA.</p>
              </div>
            </div>

            <PressableButton variant="amber" className="w-full" onClick={() => setShowLimitations(false)}>
              Acknowledge Disclosures
            </PressableButton>
          </div>
        </div>
      )}
    </div>
  );
}

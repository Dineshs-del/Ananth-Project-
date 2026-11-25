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

type ActiveTab = 'requester' | 'live_guard' | 'approver' | 'audit' | 'enrollment';

const NAV_ITEMS: { id: ActiveTab; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { id: 'requester', label: 'Requester Gate', icon: Shield },
  { id: 'live_guard', label: 'Live Guard', icon: Radio },
  { id: 'approver', label: 'Approver Phone', icon: Smartphone },
  { id: 'audit', label: 'Operations & Audit', icon: ScrollText },
  { id: 'enrollment', label: 'Voice Enrollment', icon: Users },
];

const SCENARIOS: { id: FixtureScenario; label: string }[] = [
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
    <div className="flex flex-col h-full">
      <div className="h-16 px-6 flex items-center border-b border-slate-200">
        <span className="font-display text-lg font-bold tracking-tight text-slate-900 whitespace-nowrap shrink-0">
          Voice Gate
        </span>
      </div>

      <nav className="flex-1 px-3 py-4 space-y-1">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          const active = activeTab === item.id;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => {
                setActiveTab(item.id);
                setMobileMenuOpen(false);
              }}
              className={`w-full flex items-center justify-between gap-3 px-3 py-2.5 text-sm font-medium rounded-lg transition-colors whitespace-nowrap shrink-0 cursor-pointer ${
                active
                  ? 'bg-slate-900 text-white'
                  : 'text-slate-600 hover:text-slate-900 hover:bg-slate-100'
              }`}
            >
              <span className="inline-flex items-center gap-2.5">
                <Icon className="w-4 h-4 shrink-0" />
                {item.label}
              </span>
              {item.id === 'approver' && pendingApprovals.length > 0 && (
                <span
                  className={`font-mono text-xs tabular-nums ${
                    active ? 'text-slate-200' : 'text-[#2563EB] font-semibold'
                  }`}
                >
                  {pendingApprovals.length}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      {/* Functional Engine & Fixture Controls */}
      <div className="p-4 border-t border-slate-200 space-y-4 bg-[#F8FAFC]">
        <div className="space-y-1.5">
          <div className="text-[11px] font-medium text-slate-500">Backend Engine</div>
          <div className="grid grid-cols-2 gap-1 p-1 bg-slate-200/70 rounded-lg">
            <button
              type="button"
              onClick={() => handleModeSwitch('local')}
              className={`py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap cursor-pointer ${
                backendMode === 'local'
                  ? 'bg-white text-slate-900 shadow-xs'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              Demo Engine
            </button>
            <button
              type="button"
              onClick={() => handleModeSwitch('live')}
              className={`py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap cursor-pointer ${
                backendMode === 'live'
                  ? 'bg-white text-slate-900 shadow-xs'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              Live FastAPI
            </button>
          </div>
        </div>

        {backendMode === 'local' ? (
          <div className="space-y-1.5">
            <div className="text-[11px] font-medium text-slate-500">
              Voice Fixture Outcome
            </div>
            <div className="grid grid-cols-2 gap-1 p-1 bg-slate-200/70 rounded-lg">
              {SCENARIOS.map((sc) => (
                <button
                  key={sc.id}
                  type="button"
                  onClick={() => handleScenarioChange(sc.id)}
                  className={`py-1 text-xs font-medium rounded-md transition-colors whitespace-nowrap cursor-pointer ${
                    scenario === sc.id
                      ? 'bg-white text-slate-900 shadow-xs'
                      : 'text-slate-600 hover:text-slate-900'
                  }`}
                >
                  {sc.label}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="space-y-1.5">
            <label htmlFor="api-base-url" className="block text-[11px] font-medium text-slate-500">
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
              className="w-full h-8 px-2.5 text-xs font-mono bg-white border border-slate-300 rounded-md focus:outline-none focus:border-[#2563EB]"
            />
            {liveHealthy !== null && (
              <div
                className={`text-[11px] font-medium ${
                  liveHealthy ? 'text-[#15803D]' : 'text-[#B91C1C]'
                }`}
              >
                {liveHealthy ? 'Connected to FastAPI backend' : 'Backend unreachable on URL'}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );

  return (
    <div className="min-h-screen flex bg-[#F8FAFC] text-slate-900">
      {/* Desktop Sidebar */}
      <aside className="hidden lg:block w-[252px] shrink-0 bg-white border-r border-slate-200 sticky top-0 h-screen">
        {SidebarContent}
      </aside>

      {/* Mobile Drawer */}
      {mobileMenuOpen && (
        <div className="fixed inset-0 z-50 lg:hidden flex">
          <div className="w-[260px] bg-white h-full border-r border-slate-200 relative">
            <button
              type="button"
              aria-label="Close navigation"
              onClick={() => setMobileMenuOpen(false)}
              className="absolute top-4 right-4 p-1.5 text-slate-500 hover:text-slate-900 cursor-pointer"
            >
              <X className="w-5 h-5" />
            </button>
            {SidebarContent}
          </div>
          <div
            className="flex-1 bg-slate-900/30"
            onClick={() => setMobileMenuOpen(false)}
          />
        </div>
      )}

      {/* Main Content */}
      <div className="flex-1 min-w-0 flex flex-col">
        {/* Top Bar (Breadcrumb left, 1-2 functional actions right) */}
        <header className="h-16 bg-white border-b border-slate-200 px-4 sm:px-6 flex items-center justify-between gap-6 sticky top-0 z-30">
          <div className="flex items-center gap-3 min-w-0">
            <button
              type="button"
              aria-label="Open navigation"
              onClick={() => setMobileMenuOpen(true)}
              className="lg:hidden p-2 -ml-2 text-slate-600 hover:text-slate-900 cursor-pointer"
            >
              <Menu className="w-5 h-5" />
            </button>
            <div className="flex items-center gap-2 text-sm min-w-0">
              <span className="text-slate-500 whitespace-nowrap hidden sm:inline">
                Break-Glass Voice Gate
              </span>
              <span aria-hidden="true" className="text-slate-300 hidden sm:inline">
                /
              </span>
              <span className="font-semibold text-slate-900 truncate">{activeTabLabel}</span>
            </div>
          </div>

          <div className="flex items-center gap-3 shrink-0">
            {activeTab === 'requester' && (
              <button
                type="button"
                onClick={() => setSplitPhoneView((v) => !v)}
                className={`hidden xl:inline-flex items-center gap-2 px-3.5 py-2 text-xs font-medium rounded-md border transition-colors whitespace-nowrap shrink-0 cursor-pointer ${
                  splitPhoneView
                    ? 'bg-slate-100 border-slate-300 text-slate-900'
                    : 'bg-white border-slate-200 text-slate-600 hover:text-slate-900'
                }`}
              >
                <Columns2 className="w-3.5 h-3.5" />
                {splitPhoneView ? 'Hide Phone Split' : 'Show Phone Split'}
              </button>
            )}

            <button
              type="button"
              onClick={() => {
                setActiveRequestId(null);
                setErrorMessage(null);
                setActiveTab('requester');
              }}
              className="inline-flex items-center gap-1.5 px-4 py-2 text-xs font-medium text-white bg-[#0F172A] hover:bg-slate-800 rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer"
            >
              <Plus className="w-3.5 h-3.5" />
              New Request
            </button>
          </div>
        </header>

        {/* Workspace Viewport */}
        <main className="flex-1 p-4 sm:p-6 lg:p-8 max-w-[1280px] w-full mx-auto">
          {activeTab === 'requester' && (
            <div
              className={
                splitPhoneView
                  ? 'grid grid-cols-1 xl:grid-cols-12 gap-6 items-start'
                  : 'max-w-[860px]'
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
              <div className="max-w-[420px] mx-auto mb-5 text-center">
                <h1 className="text-xl font-semibold tracking-tight text-slate-900">
                  Second-Device Approver Page
                </h1>
                <p className="mt-1 text-sm text-slate-600">
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
        </main>
      </div>
    </div>
  );
}

import React, { useState } from 'react';
import { UserPlus, Users, CheckCircle2, ShieldCheck, AlertTriangle } from 'lucide-react';
import type { Employee, EmployeeRole } from '../types';
import { AudioRecorder } from './AudioRecorder';
import { GlassCard } from './ui/GlassCard';
import { PressableButton } from './ui/PressableButton';
import { StatusBadge } from './ui/StatusBadge';

interface EnrollmentPanelProps {
  employees: Employee[];
  busy: boolean;
  onCreateEmployee: (input: {
    employee_id: string;
    display_name: string;
    role: EmployeeRole;
    known_callback_hint: string;
  }) => Promise<void>;
  onEnrollSample: (employeeId: string, file: File) => Promise<string>;
}

export const EnrollmentPanel: React.FC<EnrollmentPanelProps> = ({
  employees,
  busy,
  onCreateEmployee,
  onEnrollSample,
}) => {
  const [selectedEmpId, setSelectedEmpId] = useState(employees[0]?.employee_id || 'emp_rahul');
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [consentConfirmed, setConsentConfirmed] = useState(false);

  const [newEmpId, setNewEmpId] = useState('');
  const [newDisplayName, setNewDisplayName] = useState('');
  const [newRole, setNewRole] = useState<EmployeeRole>('requester');
  const [newCallbackHint, setNewCallbackHint] = useState('');

  const selectedEmp = employees.find((e) => e.employee_id === selectedEmpId) || employees[0];

  const handleEnroll = async (file: File) => {
    if (!selectedEmp) return;
    if (!consentConfirmed) {
      setErrorMessage('Explicit participant voice consent is required prior to enrollment.');
      return;
    }
    setErrorMessage(null);
    setStatusMessage(null);
    try {
      const msg = await onEnrollSample(selectedEmp.employee_id, file);
      setStatusMessage(msg);
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : 'Enrollment failed.');
    }
  };

  const handleAddEmployee = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage(null);
    setStatusMessage(null);
    try {
      await onCreateEmployee({
        employee_id: newEmpId.trim(),
        display_name: newDisplayName.trim(),
        role: newRole,
        known_callback_hint: newCallbackHint.trim(),
      });
      setSelectedEmpId(newEmpId.trim());
      setNewEmpId('');
      setNewDisplayName('');
      setNewCallbackHint('');
      setStatusMessage('Employee registered. Check consent box and enroll 1–5 voice samples (15–60s each).');
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : 'Could not create employee.');
    }
  };

  return (
    <div data-testid="enrollment-panel" className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start max-w-[1280px] mx-auto">
      {/* Directory & Sample Enrollment */}
      <GlassCard className="lg:col-span-7 overflow-hidden space-y-6 p-6">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <Users className="w-5 h-5 text-cyan-400" />
            <h1 className="text-xl font-bold tracking-tight text-white font-display">
              Eidos Biometric Speaker Enrollment
            </h1>
          </div>
          <p className="text-xs text-slate-400 leading-relaxed">
            Enroll 1 to 5 voice samples per team member (15–60s audio clips containing natural speech).
          </p>
        </div>

        {/* Employee Roster List */}
        <div className="space-y-2">
          <div className="text-[11px] font-mono uppercase tracking-wider text-slate-400 font-semibold">
            Team Directory &amp; Sample Capacity
          </div>
          <div className="divide-y divide-white/[0.06] border border-white/10 rounded-xl overflow-hidden bg-slate-950/60">
            {employees.map((emp) => {
              const active = selectedEmp?.employee_id === emp.employee_id;
              const isFullyEnrolled = emp.enrolled_samples >= 5;

              return (
                <button
                  key={emp.employee_id}
                  data-testid={`employee-item-${emp.employee_id}`}
                  type="button"
                  onClick={() => {
                    setSelectedEmpId(emp.employee_id);
                    setStatusMessage(null);
                    setErrorMessage(null);
                  }}
                  className={`w-full text-left px-4 py-3 flex items-center justify-between gap-4 transition-all cursor-pointer ${
                    active ? 'bg-cyan-500/15 border-l-4 border-cyan-400' : 'hover:bg-white/[0.03]'
                  }`}
                >
                  <div className="min-w-0">
                    <div className="text-sm font-semibold text-white truncate flex items-center gap-2">
                      <span>{emp.display_name}</span>
                      {isFullyEnrolled && (
                        <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">
                          MAX SAMPLES
                        </span>
                      )}
                    </div>
                    <div className="text-xs font-mono text-slate-400 mt-0.5 flex flex-wrap items-center gap-2">
                      <span className="text-cyan-400">{emp.employee_id}</span>
                      <span aria-hidden="true">·</span>
                      <span className="capitalize">{emp.role.replace('_', ' ')}</span>
                      {emp.known_callback_hint && (
                        <>
                          <span aria-hidden="true">·</span>
                          <span className="text-slate-300">{emp.known_callback_hint}</span>
                        </>
                      )}
                    </div>
                  </div>

                  {/* Sample Count Progress Indicator */}
                  <div className="text-right shrink-0">
                    <div className="font-mono text-sm font-bold tabular-nums text-white">
                      {emp.enrolled_samples} / 5
                    </div>
                    <div className="w-16 h-1 bg-slate-800 rounded-full overflow-hidden mt-1">
                      <div
                        className="h-full bg-cyan-400"
                        style={{ width: `${(emp.enrolled_samples / 5) * 100}%` }}
                      />
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* Enrollment Action Section */}
        {selectedEmp && (
          <div className="p-5 rounded-2xl bg-slate-950/80 border border-white/10 space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-sm font-bold text-white font-mono">
                  Enroll Sample for {selectedEmp.display_name}
                </h2>
                <p className="text-xs text-slate-400">
                  {5 - selectedEmp.enrolled_samples} slot(s) remaining for this identity.
                </p>
              </div>
            </div>

            {/* Explicit Consent Requirement */}
            <div className="p-3.5 rounded-xl bg-cyan-950/30 border border-cyan-500/30 space-y-2">
              <label className="flex items-start gap-2.5 text-xs text-cyan-200 cursor-pointer font-medium select-none">
                <input
                  type="checkbox"
                  data-testid="enroll-consent-checkbox"
                  checked={consentConfirmed}
                  onChange={(e) => setConsentConfirmed(e.target.checked)}
                  className="mt-0.5 rounded border-white/20 text-cyan-500 focus:ring-cyan-400"
                />
                <span>
                  I confirm explicit participant consent has been granted to record and store biometric voice embeddings for {selectedEmp.display_name}.
                </span>
              </label>
            </div>

            <AudioRecorder
              disabled={busy || selectedEmp.enrolled_samples >= 5 || !consentConfirmed}
              targetMinSeconds={15}
              actionLabel={busy ? 'Extracting Neural Embedding...' : 'Enroll Voice Sample (15–60s)'}
              onAudioReady={(file) => handleEnroll(file)}
            />

            {statusMessage && (
              <p className="p-3 rounded-xl bg-emerald-950/40 border border-emerald-500/40 text-xs font-mono text-emerald-300">
                {statusMessage}
              </p>
            )}
            {errorMessage && (
              <p className="p-3 rounded-xl bg-rose-950/40 border border-rose-500/40 text-xs font-mono text-rose-300">
                {errorMessage}
              </p>
            )}
          </div>
        )}
      </GlassCard>

      {/* Register New Team Member */}
      <GlassCard className="lg:col-span-5 p-6 space-y-5">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <UserPlus className="w-5 h-5 text-cyan-400" />
            <h2 className="text-base font-bold text-white font-display">
              Register Team Member
            </h2>
          </div>
          <p className="text-xs text-slate-400">
            Define corporate access role and verified out-of-band callback directory extension.
          </p>
        </div>

        <form onSubmit={handleAddEmployee} data-testid="add-employee-form" className="space-y-4">
          <div>
            <label htmlFor="new-emp-id" className="block text-xs font-mono text-slate-300 mb-1">
              Employee ID
            </label>
            <input
              id="new-emp-id"
              data-testid="new-emp-id-input"
              type="text"
              required
              pattern="[A-Za-z0-9_.:-]{1,64}"
              placeholder="emp_pradeep"
              value={newEmpId}
              onChange={(e) => setNewEmpId(e.target.value)}
              className="w-full h-9 px-3 text-xs font-mono bg-slate-950/80 border border-white/10 rounded-lg text-white focus:outline-none focus:border-cyan-400"
            />
          </div>

          <div>
            <label htmlFor="new-emp-name" className="block text-xs font-mono text-slate-300 mb-1">
              Display Name &amp; Title
            </label>
            <input
              id="new-emp-name"
              data-testid="new-emp-name-input"
              type="text"
              required
              placeholder="Pradeep — Site Reliability"
              value={newDisplayName}
              onChange={(e) => setNewDisplayName(e.target.value)}
              className="w-full h-9 px-3 text-xs bg-slate-950/80 border border-white/10 rounded-lg text-white focus:outline-none focus:border-cyan-400"
            />
          </div>

          <div>
            <label htmlFor="new-emp-role" className="block text-xs font-mono text-slate-300 mb-1">
              Access Role
            </label>
            <select
              id="new-emp-role"
              data-testid="new-emp-role-select"
              value={newRole}
              onChange={(e) => setNewRole(e.target.value as EmployeeRole)}
              className="w-full h-9 px-3 text-xs font-mono bg-slate-950/80 border border-white/10 rounded-lg text-white focus:outline-none focus:border-cyan-400"
            >
              <option value="requester" className="bg-slate-950">Requester (can initiate break-glass)</option>
              <option value="approver" className="bg-slate-950">Approver (can approve on 2nd device)</option>
              <option value="security_admin" className="bg-slate-950">Security Admin (peer review authority)</option>
            </select>
          </div>

          <div>
            <label htmlFor="new-emp-cb" className="block text-xs font-mono text-slate-300 mb-1">
              Known Callback Directory Hint
            </label>
            <input
              id="new-emp-cb"
              data-testid="new-emp-cb-input"
              type="text"
              placeholder="Corp Directory Ext. 4315"
              value={newCallbackHint}
              onChange={(e) => setNewCallbackHint(e.target.value)}
              className="w-full h-9 px-3 text-xs font-mono bg-slate-950/80 border border-white/10 rounded-lg text-white focus:outline-none focus:border-cyan-400"
            />
          </div>

          <PressableButton
            type="submit"
            data-testid="add-employee-submit"
            variant="primary"
            className="w-full mt-2"
            disabled={busy}
            icon={UserPlus}
          >
            Add Team Member
          </PressableButton>
        </form>
      </GlassCard>
    </div>
  );
};

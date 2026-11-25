import React, { useState } from 'react';
import { UserPlus } from 'lucide-react';
import type { Employee, EmployeeRole } from '../types';
import { AudioRecorder } from './AudioRecorder';

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

  const [newEmpId, setNewEmpId] = useState('');
  const [newDisplayName, setNewDisplayName] = useState('');
  const [newRole, setNewRole] = useState<EmployeeRole>('requester');
  const [newCallbackHint, setNewCallbackHint] = useState('');

  const selectedEmp = employees.find((e) => e.employee_id === selectedEmpId) || employees[0];

  const handleEnroll = async (file: File) => {
    if (!selectedEmp) return;
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
      setStatusMessage('Employee added. You can now enroll 1–5 voice samples (15–60s each).');
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : 'Could not create employee.');
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
      {/* Left: Directory & Sample Upload */}
      <div className="lg:col-span-7 bg-white border border-slate-200 rounded-xl divide-y divide-slate-200">
        <div className="p-6">
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">
            Eidos Speaker Enrollment
          </h1>
          <p className="mt-1 text-sm text-slate-600">
            Enroll 1–5 voice samples per employee (15–60s audio with at least 10s of natural speech). Only record voices with explicit permission.
          </p>
        </div>

        <div className="divide-y divide-slate-200">
          {employees.map((emp) => {
            const active = selectedEmp?.employee_id === emp.employee_id;
            return (
              <button
                key={emp.employee_id}
                type="button"
                onClick={() => {
                  setSelectedEmpId(emp.employee_id);
                  setStatusMessage(null);
                  setErrorMessage(null);
                }}
                className={`w-full text-left px-6 py-4 flex items-center justify-between gap-4 transition-colors cursor-pointer ${
                  active ? 'bg-blue-50/40' : 'hover:bg-slate-50'
                }`}
              >
                <div className="min-w-0">
                  <div className="text-sm font-semibold text-slate-900 truncate">
                    {emp.display_name}
                  </div>
                  <div className="text-xs text-slate-500 mt-0.5 flex items-center gap-2">
                    <span className="font-mono">{emp.employee_id}</span>
                    <span aria-hidden="true">·</span>
                    <span>{emp.role.replace('_', ' ')}</span>
                    {emp.known_callback_hint && (
                      <>
                        <span aria-hidden="true">·</span>
                        <span>{emp.known_callback_hint}</span>
                      </>
                    )}
                  </div>
                </div>
                <div className="text-right shrink-0">
                  <div className="font-mono text-sm font-semibold tabular-nums text-slate-900">
                    {emp.enrolled_samples} / 5
                  </div>
                  <div className="text-[11px] text-slate-500">samples</div>
                </div>
              </button>
            );
          })}
        </div>

        {selectedEmp && (
          <div className="p-6 space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-900">
                Add Voice Sample for {selectedEmp.display_name}
              </h2>
              <span className="font-mono text-xs text-slate-500 tabular-nums">
                {5 - selectedEmp.enrolled_samples} slot(s) left
              </span>
            </div>

            <AudioRecorder
              disabled={busy || selectedEmp.enrolled_samples >= 5}
              targetMinSeconds={15}
              actionLabel={busy ? 'Enrolling Sample...' : 'Enroll Voice Sample'}
              onAudioReady={(file) => handleEnroll(file)}
            />

            {statusMessage && (
              <p className="text-xs font-medium text-[#15803D]">{statusMessage}</p>
            )}
            {errorMessage && (
              <p className="text-xs font-medium text-[#B91C1C]">{errorMessage}</p>
            )}
          </div>
        )}
      </div>

      {/* Right: Register New Employee */}
      <div className="lg:col-span-5 bg-white border border-slate-200 rounded-xl overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-200">
          <h2 className="text-sm font-semibold text-slate-900">Register Team Member</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Store role permissions and verified corporate callback channel.
          </p>
        </div>

        <form onSubmit={handleAddEmployee} className="p-6 space-y-4">
          <div>
            <label htmlFor="new-emp-id" className="block text-xs font-medium text-slate-700 mb-1">
              Employee ID
            </label>
            <input
              id="new-emp-id"
              type="text"
              required
              pattern="[A-Za-z0-9_.:-]{1,64}"
              placeholder="emp_pradeep"
              value={newEmpId}
              onChange={(e) => setNewEmpId(e.target.value)}
              className="w-full h-9 px-3 text-sm font-mono bg-white border border-slate-300 rounded-md focus:outline-none focus:border-[#2563EB]"
            />
          </div>

          <div>
            <label htmlFor="new-emp-name" className="block text-xs font-medium text-slate-700 mb-1">
              Display Name & Title
            </label>
            <input
              id="new-emp-name"
              type="text"
              required
              placeholder="Pradeep — Site Reliability"
              value={newDisplayName}
              onChange={(e) => setNewDisplayName(e.target.value)}
              className="w-full h-9 px-3 text-sm bg-white border border-slate-300 rounded-md focus:outline-none focus:border-[#2563EB]"
            />
          </div>

          <div>
            <label htmlFor="new-emp-role" className="block text-xs font-medium text-slate-700 mb-1">
              Access Role
            </label>
            <select
              id="new-emp-role"
              value={newRole}
              onChange={(e) => setNewRole(e.target.value as EmployeeRole)}
              className="w-full h-9 px-3 text-sm bg-white border border-slate-300 rounded-md focus:outline-none focus:border-[#2563EB]"
            >
              <option value="requester">Requester (can initiate break-glass)</option>
              <option value="approver">Approver (can approve on second device)</option>
              <option value="security_admin">Security Admin (peer review & escalation)</option>
            </select>
          </div>

          <div>
            <label htmlFor="new-emp-cb" className="block text-xs font-medium text-slate-700 mb-1">
              Known Callback Directory Hint
            </label>
            <input
              id="new-emp-cb"
              type="text"
              placeholder="Corp Directory Ext. 4315"
              value={newCallbackHint}
              onChange={(e) => setNewCallbackHint(e.target.value)}
              className="w-full h-9 px-3 text-sm bg-white border border-slate-300 rounded-md focus:outline-none focus:border-[#2563EB]"
            />
          </div>

          <button
            type="submit"
            disabled={busy}
            className="w-full inline-flex items-center justify-center gap-2 h-10 px-4 text-sm font-medium text-white bg-[#0F172A] hover:bg-slate-800 disabled:opacity-50 rounded-md transition-colors cursor-pointer"
          >
            <UserPlus className="w-4 h-4" />
            Add Employee
          </button>
        </form>
      </div>
    </div>
  );
};

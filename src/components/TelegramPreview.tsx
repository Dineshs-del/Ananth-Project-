import React, { useState } from 'react';
import {
  Send,
  Bot,
  ShieldAlert,
  ShieldCheck,
  CheckCircle2,
  Clock,
  Terminal,
  Info,
  Mic,
  PhoneCall,
  XCircle,
} from 'lucide-react';
import type { BreakGlassRequest, AuditEvent } from '../types';
import { GlassCard } from './ui/GlassCard';
import { Tilt3D } from './ui/Tilt3D';
import { Float } from './ui/Float';
import { PressableButton } from './ui/PressableButton';
import { StatusBadge } from './ui/StatusBadge';

interface TelegramPreviewProps {
  activeRequest: BreakGlassRequest | null;
  auditEvents: AuditEvent[];
}

interface ChatMessage {
  id: string;
  sender: 'user' | 'bot';
  text?: string;
  time: string;
  buttons?: { text: string; action: string; variant?: 'default' | 'danger' | 'success' }[];
  audioClip?: { duration: string; filename: string };
  isAlert?: boolean;
}

export const TelegramPreview: React.FC<TelegramPreviewProps> = ({
  activeRequest,
  auditEvents,
}) => {
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: '1',
      sender: 'bot',
      text: '🤖 *Break-Glass Voice Gate Bot*\n\nSecure emergency approval bridge.\nUse commands or reply to a voice note with `/check`.',
      time: '12:00 PM',
    },
    {
      id: '2',
      sender: 'user',
      text: '/approver',
      time: '12:01 PM',
    },
    {
      id: '3',
      sender: 'bot',
      text: `🚨 *BREAK-GLASS APPROVAL REQUIRED*\n\n*Action:* ${
        activeRequest?.action_summary || 'Drain traffic & rotate TLS keys'
      }\n*Target:* \`${activeRequest?.target_resource || 'k8s-prod-us-east'}\`\n*Requester:* Rahul Sharma (emp_rahul)\n*Aletheia Verdict:* REAL (Score: 0.12)\n\n⏱ *Window:* 120s remaining\n*Tap the matching 2-digit number:*`,
      time: '12:01 PM',
      buttons: [
        { text: activeRequest?.active_approval?.number_options[0] || '42', action: 'match' },
        { text: activeRequest?.active_approval?.number_options[1] || '87', action: 'match' },
        { text: activeRequest?.active_approval?.number_options[2] || '19', action: 'match' },
        { text: '⛔ Reject Request', action: 'reject', variant: 'danger' },
        { text: '🔒 Block Request', action: 'block', variant: 'danger' },
      ],
    },
  ]);

  const [isTyping, setIsTyping] = useState(false);
  const [activeFeedback, setActiveFeedback] = useState<string | null>(null);

  const simulateBotReply = (userCmd: string, botMsg: Omit<ChatMessage, 'id' | 'sender' | 'time'>) => {
    const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    setMessages((prev) => [
      ...prev,
      { id: String(Date.now()), sender: 'user', text: userCmd, time },
    ]);
    setIsTyping(true);
    setActiveFeedback(null);

    setTimeout(() => {
      setIsTyping(false);
      setMessages((prev) => [
        ...prev,
        {
          id: String(Date.now() + 1),
          sender: 'bot',
          time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
          ...botMsg,
        },
      ]);
    }, 700);
  };

  const handleCommand = (cmd: string) => {
    switch (cmd) {
      case '/check':
        simulateBotReply('/check (voice note)', {
          text: '🔍 *Aletheia Deepfake Inspection*\n\n• *Duration:* 6.2s audio\n• *Verdict:* REAL ✅\n• *Score:* 0.08 (low synthetic artifacts)\n• *Op ID:* `op_vn_8892`\n\n_Rate limit: 4 checks remaining in 10m window._',
        });
        break;

      case '/enroll':
        simulateBotReply('/enroll', {
          text: '🎙 *Voice Enrollment Session*\n\nPlease confirm consent to record your voice samples (15–60s each, max 5 samples):',
          buttons: [
            { text: '✅ I Consent', action: 'consent', variant: 'success' },
            { text: '❌ Cancel', action: 'cancel', variant: 'danger' },
          ],
        });
        break;

      case '/start':
        simulateBotReply('/start', {
          text: '🤖 *Break-Glass Voice Gate Bot*\n\nSecure emergency approval bridge.\nCommands: `/check`, `/link`, `/enroll`, `/done`, `/approver`, `/requester`, `/report`, `/status`.\n\nReply to any voice note with `/check` to inspect for synthetic speech via Aletheia neural detection.',
        });
        break;

      case '/link':
        simulateBotReply('/link emp_rahul', {
          text: '🔗 *Account Linked*\n\nTelegram user linked to employee `emp_rahul` (Rahul Sharma).\nYou can now run `/enroll` or receive emergency approval requests.',
        });
        break;

      case '/done':
        simulateBotReply('/done', {
          text: '✅ *Voice Enrollment Finalized*\n\nEnrolled samples saved. Eidos speaker verification template is ready for break-glass operations.',
        });
        break;

      case '/requester':
        simulateBotReply('/requester', {
          text: '📋 *Requester Quick Status*\n\n• *Active Request:* `req_9812_k8s`\n• *Action:* Drain traffic & rotate TLS keys\n• *Gate State:* Awaiting voice verification',
        });
        break;

      case 'alert_push':
        setMessages((prev) => [
          ...prev,
          {
            id: String(Date.now()),
            sender: 'bot',
            time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
            isAlert: true,
            text: '🚨 *AUTOMATIC LIVE-CALL ALERT (PUSHED BY THE BOT)*\n\n• *Session:* req_live_901\n• *Status:* FAIL (Latched)\n• *Reason:* High acoustic synthetic probability detected (>0.85)\n\n*Immediate Actions:*',
            buttons: [
              { text: '🚫 Block Request', action: 'block', variant: 'danger' },
              { text: '📞 Call back on known number', action: 'callback' },
            ],
          },
        ]);
        break;

      case '/report':
        const recentEvents = auditEvents.slice(0, 5);
        const reportText = `📜 *Hash-Chain Audit Report*\n\n*Integrity:* VALID (Chain unbroken)\n*Total Chained Events:* ${auditEvents.length}\n\n*Recent Audit Entries:*\n${
          recentEvents.length > 0
            ? recentEvents
                .map(
                  (e) =>
                    `• \`#${e.id}\` *${e.event_type}* (${e.actor_id}) → \`${e.event_hash.slice(0, 8)}...\``
                )
                .join('\n')
            : '• `#1` INITIAL_KEYSTORE_SEED → `00000000...`'
        }`;
        simulateBotReply('/report', { text: reportText });
        break;

      case '/status':
        simulateBotReply('/status', {
          text: '🛡 *Break-Glass Voice Gate Status*\n\n• *Mode:* Active Bridge\n• *Enrolled Samples:* 3 / 5\n• *Deepfake Engine:* Aletheia v1.2\n• *Speaker Engine:* Eidos v2.0\n• *Rate Limit:* Healthy (0/5 used)',
        });
        break;

      default:
        simulateBotReply(cmd, {
          text: `Command \`${cmd}\` recognized by BreakGlassBot.`,
        });
    }
  };

  const handleButtonClick = (action: string, btnText: string) => {
    if (action === 'match') {
      setActiveFeedback(`Tapped matching code "${btnText}" — Approval verified!`);
    } else if (action === 'consent') {
      setActiveFeedback('Consent recorded. You may now send a 15–60s voice message to enroll.');
    } else if (action === 'block' || action === 'reject') {
      setActiveFeedback(`Action "${btnText}" executed. Request blocked in audit ledger.`);
    } else if (action === 'callback') {
      setActiveFeedback('Callback recommended: Verify identity on internal Corp Directory Ext.');
    } else {
      setActiveFeedback(`Triggered: ${btnText}`);
    }
  };

  return (
    <div data-testid="telegram-preview-tab" className="space-y-6 max-w-[1040px] mx-auto">
      {/* Simulation Banner Notice */}
      <div className="p-4 rounded-2xl bg-cyan-950/40 border border-cyan-500/40 backdrop-blur-xl flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 flex items-center justify-center shrink-0">
            <Bot className="w-5 h-5" />
          </div>
          <div>
            <div className="text-xs font-mono font-bold uppercase tracking-wider text-cyan-300">
              Simulated preview — mirrors the Telegram bot&apos;s message formats
            </div>
            <p className="text-[11px] text-slate-400 mt-0.5">
              Reflects only implemented bot features: DM /check (Aletheia-only), /enroll consent, /approver console, /report audit, and live-call alerts.
            </p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
        {/* Telegram Phone Simulator Frame */}
        <div className="lg:col-span-7 flex justify-center">
          <Float duration={5} distance={5}>
            <div className="w-full max-w-[390px] rounded-[44px] p-3 bg-gradient-to-b from-slate-800 to-slate-950 border-4 border-slate-700/80 shadow-[0_20px_50px_rgba(0,0,0,0.8)] ring-1 ring-white/10 select-none">
              {/* Telegram Phone Screen */}
              <div className="rounded-[34px] bg-[#0c1424] border border-white/10 overflow-hidden flex flex-col h-[580px] text-white">
                {/* Telegram Header */}
                <div className="px-4 py-3 bg-[#172136] border-b border-white/5 flex items-center justify-between">
                  <div className="flex items-center gap-2.5">
                    <div className="w-9 h-9 rounded-full bg-gradient-to-tr from-cyan-500 to-blue-600 flex items-center justify-center font-bold text-white shadow-sm">
                      <Bot className="w-5 h-5" />
                    </div>
                    <div>
                      <div className="text-xs font-bold font-sans flex items-center gap-1">
                        <span>BreakGlassBot</span>
                        <CheckCircle2 className="w-3.5 h-3.5 text-cyan-400 fill-current" />
                      </div>
                      <div className="text-[10px] text-cyan-400/80 font-mono">bot</div>
                    </div>
                  </div>
                  <div className="text-[11px] font-mono text-slate-400">safe-channel</div>
                </div>

                {/* Messages Body */}
                <div className="flex-1 p-3.5 space-y-3 overflow-y-auto font-sans text-xs">
                  {messages.map((m) => (
                    <div
                      key={m.id}
                      className={`flex flex-col ${m.sender === 'user' ? 'items-end' : 'items-start'}`}
                    >
                      <div
                        className={`max-w-[85%] p-3 rounded-2xl space-y-2 shadow-md leading-relaxed ${
                          m.sender === 'user'
                            ? 'bg-cyan-600 text-white rounded-tr-xs font-mono'
                            : m.isAlert
                              ? 'bg-rose-950/80 text-rose-100 border border-rose-500/40 rounded-tl-xs'
                              : 'bg-[#18253d] text-slate-100 border border-white/5 rounded-tl-xs'
                        }`}
                      >
                        <div className="whitespace-pre-wrap">{m.text}</div>

                        {/* Inline Telegram Keyboard Buttons */}
                        {m.buttons && (
                          <div className="grid grid-cols-2 gap-1.5 pt-2 border-t border-white/10">
                            {m.buttons.map((btn, bIdx) => (
                              <button
                                key={bIdx}
                                type="button"
                                onClick={() => handleButtonClick(btn.action, btn.text)}
                                className={`py-1.5 px-2 text-[11px] font-mono rounded-lg transition-all text-center cursor-pointer ${
                                  btn.variant === 'danger'
                                    ? 'bg-rose-500/20 text-rose-300 border border-rose-500/40 hover:bg-rose-500/30'
                                    : btn.variant === 'success'
                                      ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 hover:bg-emerald-500/30'
                                      : 'bg-white/10 hover:bg-white/15 text-cyan-300 border border-white/10'
                                }`}
                              >
                                {btn.text}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                      <span className="text-[9px] text-slate-500 font-mono mt-0.5 px-1">{m.time}</span>
                    </div>
                  ))}

                  {/* Typing Indicator */}
                  {isTyping && (
                    <div className="flex items-center gap-1.5 p-2.5 rounded-xl bg-[#18253d] text-slate-400 w-fit">
                      <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-bounce" />
                      <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-bounce [animation-delay:0.2s]" />
                      <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-bounce [animation-delay:0.4s]" />
                      <span className="text-[10px] font-mono ml-1">bot is typing...</span>
                    </div>
                  )}

                  {activeFeedback && (
                    <div className="p-2 rounded-lg bg-emerald-950/60 border border-emerald-500/40 text-[10px] font-mono text-emerald-300 text-center animate-fadeIn">
                      {activeFeedback}
                    </div>
                  )}
                </div>

                {/* Telegram Input Bar */}
                <div className="p-2.5 bg-[#172136] border-t border-white/5 flex items-center gap-2">
                  <div className="flex-1 h-9 px-3 rounded-full bg-[#0c1424] border border-white/10 text-xs text-slate-400 flex items-center justify-between">
                    <span>Message...</span>
                    <Mic className="w-4 h-4 text-cyan-400 cursor-pointer" onClick={() => handleCommand('/check')} />
                  </div>
                  <button
                    type="button"
                    onClick={() => handleCommand('/status')}
                    className="w-9 h-9 rounded-full bg-cyan-500 text-white flex items-center justify-center cursor-pointer shadow-md hover:bg-cyan-400"
                  >
                    <Send className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>
          </Float>
        </div>

        {/* Command Cheat-Sheet & Interactive Chips */}
        <div className="lg:col-span-5 space-y-4">
          <GlassCard className="p-6 space-y-4">
            <div className="flex items-center gap-2 text-cyan-400 font-mono text-xs uppercase tracking-wider font-semibold">
              <Terminal className="w-4 h-4" />
              Interactive Bot Commands
            </div>
            <p className="text-xs text-slate-300 leading-relaxed">
              Click any command chip below to trigger the simulated bot response and experience the production Telegram workflow:
            </p>

            <div className="space-y-2">
              <div className="text-[11px] font-mono uppercase tracking-wider text-slate-400 font-semibold mb-1">
                Real Bot Commands
              </div>

              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => handleCommand('/start')}
                  className="p-2.5 rounded-xl bg-slate-950/70 border border-white/10 hover:border-cyan-400 hover:bg-cyan-500/10 transition-all cursor-pointer text-left"
                >
                  <div className="text-xs font-mono font-bold text-cyan-300">/start</div>
                  <div className="text-[10px] text-slate-400">Welcome &amp; overview</div>
                </button>

                <button
                  type="button"
                  onClick={() => handleCommand('/check')}
                  className="p-2.5 rounded-xl bg-slate-950/70 border border-white/10 hover:border-cyan-400 hover:bg-cyan-500/10 transition-all cursor-pointer text-left"
                >
                  <div className="text-xs font-mono font-bold text-cyan-300">/check</div>
                  <div className="text-[10px] text-slate-400">Aletheia neural check</div>
                </button>

                <button
                  type="button"
                  onClick={() => handleCommand('/link')}
                  className="p-2.5 rounded-xl bg-slate-950/70 border border-white/10 hover:border-cyan-400 hover:bg-cyan-500/10 transition-all cursor-pointer text-left"
                >
                  <div className="text-xs font-mono font-bold text-cyan-300">/link</div>
                  <div className="text-[10px] text-slate-400">Bind employee identity</div>
                </button>

                <button
                  type="button"
                  onClick={() => handleCommand('/enroll')}
                  className="p-2.5 rounded-xl bg-slate-950/70 border border-white/10 hover:border-cyan-400 hover:bg-cyan-500/10 transition-all cursor-pointer text-left"
                >
                  <div className="text-xs font-mono font-bold text-cyan-300">/enroll</div>
                  <div className="text-[10px] text-slate-400">Consent &amp; voice sample</div>
                </button>

                <button
                  type="button"
                  onClick={() => handleCommand('/done')}
                  className="p-2.5 rounded-xl bg-slate-950/70 border border-white/10 hover:border-cyan-400 hover:bg-cyan-500/10 transition-all cursor-pointer text-left"
                >
                  <div className="text-xs font-mono font-bold text-cyan-300">/done</div>
                  <div className="text-[10px] text-slate-400">Finalize enrollment</div>
                </button>

                <button
                  type="button"
                  onClick={() => handleCommand('/approver')}
                  className="p-2.5 rounded-xl bg-slate-950/70 border border-white/10 hover:border-cyan-400 hover:bg-cyan-500/10 transition-all cursor-pointer text-left"
                >
                  <div className="text-xs font-mono font-bold text-cyan-300">/approver</div>
                  <div className="text-[10px] text-slate-400">Number-match console</div>
                </button>

                <button
                  type="button"
                  onClick={() => handleCommand('/requester')}
                  className="p-2.5 rounded-xl bg-slate-950/70 border border-white/10 hover:border-cyan-400 hover:bg-cyan-500/10 transition-all cursor-pointer text-left"
                >
                  <div className="text-xs font-mono font-bold text-cyan-300">/requester</div>
                  <div className="text-[10px] text-slate-400">Active request status</div>
                </button>

                <button
                  type="button"
                  onClick={() => handleCommand('/report')}
                  className="p-2.5 rounded-xl bg-slate-950/70 border border-white/10 hover:border-cyan-400 hover:bg-cyan-500/10 transition-all cursor-pointer text-left"
                >
                  <div className="text-xs font-mono font-bold text-cyan-300">/report</div>
                  <div className="text-[10px] text-slate-400">Hash-chain audit summary</div>
                </button>

                <button
                  type="button"
                  onClick={() => handleCommand('/status')}
                  className="p-2.5 rounded-xl bg-slate-950/70 border border-white/10 hover:border-cyan-400 hover:bg-cyan-500/10 transition-all cursor-pointer text-left col-span-2"
                >
                  <div className="text-xs font-mono font-bold text-cyan-300">/status</div>
                  <div className="text-[10px] text-slate-400">Query authorization &amp; rate limit window</div>
                </button>
              </div>

              <div className="pt-2">
                <button
                  type="button"
                  onClick={() => handleCommand('alert_push')}
                  className="w-full text-left p-3 rounded-xl bg-rose-950/30 border border-rose-500/30 hover:border-rose-400 hover:bg-rose-500/15 transition-all cursor-pointer space-y-0.5"
                >
                  <div className="text-xs font-mono font-bold text-rose-400">Automatic live-call alert (pushed by the bot)</div>
                  <div className="text-[11px] text-slate-400">
                    Simulate alert pushed by bot on detected acoustic anomaly.
                  </div>
                </button>
              </div>
            </div>
          </GlassCard>

          <GlassCard className="p-4 space-y-1 text-xs font-mono text-slate-400">
            <span className="text-slate-300 font-bold block">Implementation Note:</span>
            <p>
              Does not show Telegram challenge-response, Kannada/Hindi reply mode, or automatic group scanning because those features are not implemented in the current backend.
            </p>
          </GlassCard>
        </div>
      </div>
    </div>
  );
};

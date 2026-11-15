export function setupLiveVoice({Streamlit,Conversation,navigator,crypto,document,window}) {
const start = document.getElementById('start');
const end = document.getElementById('end');
const status = document.getElementById('status');
let conversation = null, pending = null, starting = false;
const used = new Set();
const report = action => Streamlit.setComponentValue({action,request_id:pending});
function ready(message) {
  starting = false; start.disabled = false; end.disabled = !conversation;
  status.textContent = message;
}
start.onclick = async () => {
  if (starting || conversation) return;
  starting = true; start.disabled = true;
  status.textContent = 'Requesting microphone permission…';
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error();
    // Explicit click precedes permission. Do not inspect or buffer audio.
    const permission = await navigator.mediaDevices.getUserMedia({audio:true});
    permission.getTracks().forEach(track => track.stop());
    pending = crypto.randomUUID();
    end.disabled = false;
    status.textContent = 'Connecting…';
    report('start');
  } catch {
    ready('Microphone unavailable or permission denied. Use text or reviewed recording.');
  }
};
end.onclick = async () => {
  const current = conversation;
  conversation = null; starting = false;
  if (pending) used.add(pending); // Ignore a late URL after cancellation.
  try { if (current) await current.endSession(); } catch { /* No payload logging. */ }
  report('ended');
  ready('Conversation ended. Start again when ready.');
};
Streamlit.events.addEventListener(Streamlit.RENDER_EVENT, async event => {
  const args = event.detail.args;
  Streamlit.setFrameHeight(190);
  if (args.error && args.request_id === pending && starting) {
    used.add(pending);
    ready('Connection failed. Try Start again, or use text/reviewed recording.');
    return;
  }
  if (!args.signed_url || args.request_id !== pending || used.has(pending) || !starting) return;
  used.add(pending); // Rerenders must never start another conversation.
  const request = pending;
  try {
    const connected = await Conversation.startSession({
      signedUrl:args.signed_url,connectionType:'websocket',
      onConnect:() => { status.textContent = 'Live conversation active · microphone on'; },
      onDisconnect:() => { conversation = null; report('ended'); ready('Disconnected. Start again or use text.'); },
      onError:() => { status.textContent = 'Connection error. End the conversation and try again, or use text.'; },
      onModeChange:({mode}) => { status.textContent = mode === 'speaking' ? 'Agent speaking · microphone on' : 'Listening · microphone on'; },
      // Deliberately no onMessage or debug/transcript/audio callbacks.
    });
    if (!starting || pending !== request) { await connected.endSession(); return; }
    conversation = connected; starting = false; start.disabled = true; end.disabled = false;
    report('connected');
  } catch {
    conversation = null;
    ready('Could not connect. Check microphone permissions or use text/reviewed recording.');
    report('failed');
  }
});
window.addEventListener('pagehide',() => { if (conversation) conversation.endSession().catch(() => {}); });
Streamlit.setComponentReady();
Streamlit.setFrameHeight(190);

}

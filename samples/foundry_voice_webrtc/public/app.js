import { PcmAudio, validateAudioSession } from './pcm-audio.mjs';
import { RetainedHistory } from './retained-history.mjs';
import { returnedCallCost } from './call-cost.mjs';

const $ = (id) => document.getElementById(id);
let config, call = null;
const messages = new Map();
const modelChoices = ['gpt-realtime-2.1', 'gpt-realtime-2.1-mini', 'azure-realtime', 'azure-realtime-flash-2026-10-14', 'gpt-5.4', 'gpt-live-1'];
let preparingAgent = false;
let agentAvailability = 'unknown';
let savedModel = null, savedVersion = null, savedTranscription = null, latestDownload = null, downloading = false;
let agentSelection = 'base';
try { if (sessionStorage.getItem('voice-agent-selection') === 'live') agentSelection = 'live'; } catch {}

function agentUrl(path, selection = agentSelection) {
  return selection === 'live' ? `${path}?agent_selection=live` : path;
}

function updateAgentControls() {
  const busy = Boolean(call) || preparingAgent;
  const live = $('agent-model-choice').value === 'gpt-live-1';
  const switching = Boolean(savedModel) && live !== (savedModel === 'gpt-live-1');
  $('start').disabled = busy || switching || !config?.configured || agentAvailability === 'unknown';
  $('start').textContent = agentAvailability === 'missing' ? 'Create Agent & start' :
    agentAvailability === 'error' ? 'Retry setup & start' : 'Start conversation';
  $('recreate-agent').disabled = busy || savedModel === 'gpt-live-1' || !config?.configured || !config?.agentRecreateAllowed || agentAvailability !== 'ready';
  $('agent-model-choice').disabled = busy || agentAvailability !== 'ready';
  $('agent-transcription-choice').disabled = busy || agentAvailability !== 'ready' || live || switching;
  $('save-model').textContent = switching ? live ? 'Create or use GPT Live Agent' : 'Use realtime Agent' : 'Save new version';
  $('save-model').disabled = busy || agentAvailability !== 'ready' || !savedVersion ||
    !modelChoices.includes($('agent-model-choice').value) ||
    (!live && !switching && !$('agent-transcription-choice').value) ||
    ($('agent-model-choice').value === savedModel && (live || $('agent-transcription-choice').value === savedTranscription));
  $('show-settings').disabled = preparingAgent || agentAvailability !== 'ready';
  $('download-conversation').disabled = Boolean(call) || downloading || !latestDownload;
  $('transport').disabled = busy || live || savedModel === 'gpt-live-1';
}

function updateVoiceChoices(model) {
  const native = model === 'azure-realtime';
  const live = model === 'gpt-live-1';
  $('voice').hidden = native || live;
  $('native-voice').hidden = !native;
  $('live-voice-info').hidden = !live;
  for (const id of ['voice', 'native-voice']) $(id).disabled = Boolean(call) || $(id).hidden;
  $('voice-label').hidden = live;
  $('voice-label').htmlFor = native ? 'native-voice' : 'voice';
  $('live-model-hint').hidden = !live;
  if (live) {
    $('transport').value = 'websocket';
    $('transport').onchange?.();
  }
}

function applyAgentDetails(result) {
  if (result.agentSelection === 'base' || result.agentSelection === 'live') {
    agentSelection = result.agentSelection;
    try { sessionStorage.setItem('voice-agent-selection', agentSelection); } catch {}
  }
  if (typeof result.agent === 'string') {
    config.agent = result.agent;
    $('agent').textContent = result.agent;
  }
  savedModel = result.modelChoice || (typeof result.model === 'string' ? result.model : null);
  savedVersion = typeof result.version === 'string' ? result.version : null;
  savedTranscription = typeof result.transcriptionModel === 'string' ? result.transcriptionModel : null;
  $('agent-transcription').textContent = savedModel === 'gpt-live-1' ? 'Built into GPT Live' : savedTranscription ?
    savedTranscription + (result.exists === false ? ' (after setup)' : '') : 'Unavailable';
  $('agent-transcription-choice').value = savedTranscription || (savedModel === 'gpt-live-1' ? config.defaultTranscriptionModel || 'mai-transcribe-2' : '');
  $('agent-version').textContent = savedVersion || (result.exists === false ? 'Not created' : 'Unavailable');
  $('agent-model-choice').value = modelChoices.includes(savedModel) ?
    savedModel : 'gpt-realtime-2.1';
  updateVoiceChoices(savedModel);
  $('agent-model').textContent = typeof result.model === 'string' && result.model.trim() ?
    result.model + (result.exists === false ? ' (after setup)' : '') : 'Unavailable';
  $('weather-tool-hint').hidden = !(result.weatherEnabled || result.exists === false);
  $('text-help').textContent = savedModel === 'gpt-live-1' ?
    'Send text to the GPT Live Responses backend. Voice audio stays active, including billable silence while muted.' :
    "Start a conversation to send text. Press Enter or Send for a text-only reply; wait for the agent's response before sending another message. Mute the mic to use text only.";
  $('history-help').textContent = savedModel === 'gpt-live-1' ?
    'GPT Live captions are timed speech fragments, not complete turns or authoritative retained history. Listening and speaking can overlap.' :
    savedModel === 'azure-realtime-flash-2026-10-14' ?
    'Automatic interruption truncation is disabled for this model. Interrupted replies may still show text that was not heard; the transcript is not authoritative retained history.' :
    "Replies stream here as live drafts. After an interruption, only the service's retained text remains; unheard text is removed.";
  if (typeof result.personalizationRequired === 'boolean') {
    config.personalizationRequired = result.personalizationRequired;
    if (result.personalizationRequired) $('personalize').checked = true;
    $('personalize').disabled = result.personalizationRequired;
  }
}

async function saveAgentModel() {
  if (call || preparingAgent || agentAvailability !== 'ready' || !savedVersion) return;
  const model = $('agent-model-choice').value;
  const switching = (model === 'gpt-live-1') !== (savedModel === 'gpt-live-1');
  preparingAgent = true;
  updateAgentControls();
  $('error').hidden = true;
  try {
    const response = await fetch(agentUrl(switching ? '/api/agent/switch' : '/api/agent/model'), {method:'POST', signal:AbortSignal.timeout(95000),
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({model, version:savedVersion,
        ...(model === 'gpt-live-1' || switching ? {} : {transcriptionModel:$('agent-transcription-choice').value})})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not save the model version.');
    applyAgentDetails(result);
    if (switching) {
      latestDownload = null;
      $('download-status').textContent = '';
    }
    $('agent-preparation').textContent = switching ?
      `${result.agent} is ready. The previous Agent was not changed.` : `Version ${savedVersion} is saved and ready.`;
  } catch (error) {
    showError(error.message);
  } finally {
    preparingAgent = false;
    updateAgentControls();
  }
}

async function showAgentSettings() {
  $('show-settings').disabled = true;
  try {
    const response = await fetch(agentUrl('/api/agent/settings'), {signal:AbortSignal.timeout(40000)});
    const result = await response.json();
    if (!response.ok || typeof result.yaml !== 'string') throw new Error(result.error || 'Could not read Agent settings.');
    $('settings-yaml').textContent = result.yaml;
    $('settings-version').textContent = `Model: ${result.model || 'Unavailable'} · Transcription: ${result.transcriptionModel || 'Unavailable'} · Version: ${result.version || 'Unavailable'}`;
    $('settings-dialog').showModal();
  } catch (error) {
    showError(error.message);
  } finally {
    updateAgentControls();
  }
}

async function downloadConversation() {
  if (call || downloading || !latestDownload) return;
  const download = latestDownload;
  downloading = true;
  updateAgentControls();
  $('download-status').textContent = 'Preparing your log and audio. Azure may need a minute to finalize the recording...';
  try {
    const response = await fetch(agentUrl('/api/conversation/download', download.agentSelection), {method:'POST',
      headers:{'Content-Type':'application/json'}, signal:AbortSignal.timeout(95000),
      body:JSON.stringify({downloadToken:download.downloadToken})});
    if (!response.ok) {
      const result = await response.json();
      throw new Error(result.error || 'Could not download the recording.');
    }
    if (!response.headers.get('Content-Type')?.startsWith('application/zip')) throw new Error('The server did not return a conversation archive.');
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `conversation-${download.conversationId}.zip`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    $('download-status').textContent = 'Download ready: conversation.json and audio.wav. Keep this recording private.';
  } catch (error) {
    $('download-status').textContent = 'Download failed. The recording was not removed from Azure.';
    showError(error.message);
  } finally {
    downloading = false;
    updateAgentControls();
  }
}

async function inspectAgent() {
  preparingAgent = true;
  updateAgentControls();
  $('agent-preparation').textContent = 'Checking for your saved Agent...';
  $('agent-model').textContent = 'Checking...';
  try {
    const response = await fetch(agentUrl('/api/agent'), {signal:AbortSignal.timeout(95000)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not check the saved Agent.');
    if (typeof result.exists !== 'boolean') throw new Error('The server returned an invalid Agent status.');
    agentAvailability = result.exists ? 'ready' : 'missing';
    applyAgentDetails(result);
    $('agent-preparation').textContent = result.exists ?
      'Your Agent is saved and ready.' :
      'No Agent yet. Starting a conversation will create and save one for next time.';
    state(result.exists ? 'Ready when you are' : 'Create your first Agent',
      result.exists ? 'Start a conversation with your saved Agent.' : 'Your Agent will be created and saved before the microphone starts.',
      result.exists ? 'Saved Agent' : 'No Agent yet');
  } catch (error) {
    agentAvailability = 'error';
    $('agent-model').textContent = 'Unavailable';
    $('agent-transcription').textContent = 'Unavailable';
    $('agent-preparation').textContent = 'Could not check the Agent. No Agent was created.';
    state('Agent setup needs attention', 'Review the error, then retry setup.', 'Setup error');
    showError(error.message);
  } finally {
    preparingAgent = false;
    updateAgentControls();
  }
}

async function prepareAgent(recreate = false) {
  if (call) throw new Error('End the conversation before changing the Agent.');
  if (preparingAgent) throw new Error('Agent preparation is already running.');
  if (recreate) {
    if (!config.agentRecreateAllowed) throw new Error('Agent recreation is disabled by the server.');
    if (agentAvailability !== 'ready') throw new Error('Start a conversation to set up the Agent before resetting it.');
    const confirmation = window.prompt(
      `Permanently remove "${config.agent}" and its saved versions, then create a fresh demo Agent under the same name? Other clients using it may be affected. Type the exact Agent name to confirm.`
    );
    if (confirmation === null) return;
    if (confirmation !== config.agent) throw new Error('Agent name did not match. Nothing was removed.');
  }
  preparingAgent = true;
  updateAgentControls();
  $('error').hidden = true;
  $('agent-preparation').textContent = recreate ? 'Resetting your Agent and preparing web search tools...' :
    agentAvailability === 'missing' ? 'Preparing web search tools, then creating and saving your Agent...' : 'Getting your Agent ready...';
  try {
    const response = await fetch(agentUrl(recreate ? '/api/agent/recreate' : '/api/agent/prepare'), {
      method:'POST', signal:AbortSignal.timeout(95000),
      ...(recreate ? {headers:{'Content-Type':'application/json'}, body:JSON.stringify({confirm_agent:config.agent})} : {}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not prepare the Agent. Check server configuration and permissions.');
    agentAvailability = 'ready';
    applyAgentDetails(result);
    $('agent-preparation').textContent = result.recreated ? 'Your Agent has been reset and saved.' : 'Your Agent is saved and ready.';
    state('Ready when you are', 'Start a conversation with your saved Agent.', 'Saved Agent');
  } catch (error) {
    agentAvailability = 'error';
    $('agent-model').textContent = 'Unavailable';
    $('agent-transcription').textContent = 'Unavailable';
    $('agent-preparation').textContent = 'Agent preparation failed.';
    throw error;
  } finally {
    preparingAgent = false;
    updateAgentControls();
  }
}

function updateTextInput() {
  const connected = call?.ready && !call.closing && call.ws?.readyState === WebSocket.OPEN;
  const enabled = connected && !call.responding && (call.live || !call.userSpeaking);
  $('text-input').disabled = !connected;
  $('send-text').disabled = !enabled || !$('text-input').value.trim();
}
function sendText(event) {
  event.preventDefault();
  const session = call;
  const text = $('text-input').value.trim();
  if (!session?.ready || session.closing || session.ws?.readyState !== WebSocket.OPEN || session.responding || (!session.live && session.userSpeaking) || !text) return;
  if (text.length > 4000) {
    showError('Text input must contain at most 4000 characters.');
    return;
  }
  const id = crypto.randomUUID().replaceAll('-', '');
  try {
    if (session.live) {
      session.ws.send(JSON.stringify({type:'response.item.create', item:{
        type:'message', role:'user', content:[{type:'input_text', text}]
      }}));
      session.ws.send(JSON.stringify({type:'response.create'}));
    } else {
      session.ws.send(JSON.stringify({type:'conversation.item.create', item:{
        id, type:'message', role:'user', content:[{type:'input_text', text}]
      }}));
      session.ws.send(JSON.stringify({type:'response.create', response:{output_modalities:['text']}}));
    }
  } catch (error) {
    fail(session, error);
    return;
  }
  session.responding = true;
  if (session.live) transcript({role:'user', text, status:'complete'});
  else session.history.user(id, text);
  $('text-input').value = '';
  updateTextInput();
}
function showSessionReference(session) {
  $('session-reference').textContent = session.sessionId ?
    `Session ID: ${session.sessionId}` : 'Session ID: unavailable (Azure did not return one).';
  $('session-reference').hidden = false;
}
function showError(message) {
  $('error').textContent = message; $('error').hidden = false;
  if (call) showSessionReference(call);
}
function state(title, hint, badge) {
  $('status').textContent = title; $('hint').textContent = hint; $('connection').textContent = badge;
}
function transcript(record) {
  const {role, text, status} = record;
  if (!text && ['live', 'complete'].includes(status) && !messages.has(record)) return;
  $('empty')?.remove();
  let entry = messages.get(record);
  if (!entry) {
    const box = document.createElement('div'); box.className = `message ${role}`;
    const label = document.createElement('label');
    const body = document.createElement('p'); box.append(label, body); $('transcript').append(box);
    entry = {box, label, body};
    messages.set(record, entry);
  }
  const labels = {
    caption:'Agent - live caption (not retained history)',
    backend:'Agent - Responses backend draft', backendComplete:'Agent - Responses backend',
    backendIncomplete:'Agent - Responses backend (incomplete)',
    live:'Agent - live draft', complete:'Agent', truncated: text ? 'Agent - retained after interruption' : 'Agent - interrupted, no text retained',
    pending:'Agent - interrupted, retrieving retained text', unavailable:'Agent - retained history unavailable',
  };
  entry.label.textContent = role === 'user' ? 'You' : labels[status];
  entry.body.textContent = text;
  entry.body.className = status === 'transcribing' ? 'transcribing' : '';
  entry.body.setAttribute('aria-busy', String(status === 'transcribing'));
  $('transcript').scrollTop = $('transcript').scrollHeight;
}
function createHistory(session) {
  return new RetainedHistory({
    render:transcript,
    remove:record => { messages.get(record)?.box.remove(); messages.delete(record); },
    retrieve:(id, eventId) => {
      if (call !== session || session.ws?.readyState !== WebSocket.OPEN) throw new Error('The connection is closed.');
      session.ws.send(JSON.stringify({type:'conversation.item.retrieve', item_id:id, event_id:eventId}));
    },
    error:showError,
  });
}
function stop(title = 'Conversation ended', hint = 'Start again whenever you’re ready.') {
  const old = call; call = null;
  if (old) {
    showSessionReference(old);
    if (old.beganAt !== undefined) showCallCost(old);
    clearTimeout(old.timeout); clearTimeout(old.disconnectTimer); clearTimeout(old.closeTimer); clearInterval(old.clock);
    old.history?.dispose();
    old.abort.abort(); old.ws?.close(); old.peer?.close(); old.audio?.dispose();
    old.mic?.getTracks().forEach(t => t.stop()); old.remote?.getTracks().forEach(t => t.stop());
  }
  $('remote-audio').pause(); $('remote-audio').srcObject = null;
  $('orb').className = 'orb'; $('play').hidden = true;
  updateAgentControls(); $('stop').disabled = true; $('mute').disabled = true;
  $('transport').disabled = savedModel === 'gpt-live-1';
  $('personalization').disabled = false;
  updateVoiceChoices(savedModel);
  $('mute').textContent = 'Mute mic'; $('mute').setAttribute('aria-pressed', 'false');
  updateTextInput();
  state(title, hint, 'Offline');
}
function showCallCost(session) {
  const seconds = Math.max(0, ((session.endedAt ?? Date.now()) - session.beganAt) / 1000);
  const result = returnedCallCost(session.returnedCost, seconds);
  $('call-cost').hidden = false;
  $('call-cost-total-label').textContent = result.status === 'partial' ? 'Priced subtotal (partial)' : 'API-estimated call total';
  $('call-cost-total').textContent = result.available ? `$${result.total.toFixed(6)} USD` : 'Unavailable';
  $('call-cost-minute').textContent = result.available && result.perMinute !== null ?
    `$${result.perMinute.toFixed(6)} USD/min${result.status === 'partial' ? ' (partial)' : ''}` : 'Unavailable';
  $('call-cost-note').textContent = result.available ?
    `${seconds.toFixed(1)} connected seconds · ${session.model}. ${result.status === 'partial' ? 'Partial: some components are not priced.' : 'Complete API estimate.'} ` +
    'Returned by the saved Agent API from finalized usage at public retail rates, not your negotiated Azure invoice.' :
    result.reason;
  $('call-cost-details').textContent = result.available ?
    `Price version: ${result.priceVersion}. Unpriced components: ${result.unpriced.join(', ') || 'none reported'}. ` +
    'Cost/minute is the returned session amount divided by connected call minutes, excluding connection setup and the final close wait. ' +
    'No assumed token counts, silence discount or local pricing model is applied. App hosting, networking, storage and other charges outside the API estimate are not added.' :
    'No duration-based price or sum of response costs is substituted. GPT Live uses a separate protocol and may return voice duration without a monetary cost.';
}
function endCall() {
  const session = call;
  if (session?.closing) return;
  if (session && session.endedAt === undefined) session.endedAt = Date.now();
  if (!session?.ready || session.ws?.readyState !== WebSocket.OPEN) return stop();
  session.closing = true;
  session.ready = false;
  session.mic?.getAudioTracks().forEach(track => { track.enabled = false; });
  session.audio?.mute(true);
  $('stop').disabled = true;
  $('mute').disabled = true;
  updateTextInput();
  $('remote-audio').pause();
  state('Ending conversation', 'Waiting for the Agent API to finalize usage and cost.', 'Closing');
  session.ws.send(JSON.stringify({type:'session.close'}));
  session.closeTimer = setTimeout(() => {
    stop('Conversation ended', 'The Agent API did not confirm final session usage.');
    showError('Agent close acknowledgment timed out; final usage and cost may be unavailable.');
  }, 25000);
}
function fail(session, error) {
  if (call !== session) return;
  stop('Couldn’t connect', 'Review the message below, then try again.');
  showError(error.message || String(error));
}
async function playAudio() {
  try {
    if (call?.audio) await call.audio.context.resume();
    else await $('remote-audio').play();
    $('play').hidden = true;
  }
  catch { if (call) $('play').hidden = false; }
}
function markReady(session) {
  if (call !== session || !session.configured || (session.transport === 'webrtc' && session.peer?.connectionState !== 'connected')) return;
  session.ready = true;
  session.mic?.getAudioTracks().forEach(t => { t.enabled = !session.muted; });
  clearTimeout(session.timeout); clearTimeout(session.disconnectTimer);
  $('mute').disabled = false; $('orb').className = 'orb active';
  updateTextInput();
  state('Listening', session.live ? 'Speak or type. GPT Live can listen and speak at the same time.' :
    'Say hello or send a text message. Your agent will respond.', 'Connected');
  if (!session.clock) {
    const began = Date.now();
    session.beganAt = began;
    session.model ??= savedModel;
    $('call-cost').hidden = true;
    session.clock = setInterval(() => {
      const seconds = Math.floor((Date.now() - began) / 1000);
      $('elapsed').textContent = `${String(Math.floor(seconds / 60)).padStart(2,'0')}:${String(seconds % 60).padStart(2,'0')}`;
    }, 1000);
  }
}
function openSocket(session, onOpen) {
  const ws = session.ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/voice?transport=${session.transport}${session.agentSelection === 'live' ? '&agent_selection=live' : ''}${session.live ? '&protocol=live' : ''}${session.personalization ? '&personalize=true' : ''}${session.voice ? `&voice=${encodeURIComponent(session.voice)}` : ''}`);
  ws.onopen = () => {
    if (call !== session) return;
    if (session.personalization) ws.send(JSON.stringify(session.personalization));
    onOpen?.(ws);
  };
  let queue = Promise.resolve();
  ws.onmessage = e => { queue = queue.then(() => handleEvent(session, JSON.parse(e.data))).catch(error => fail(session,error)); };
  ws.onerror = () => fail(session,new Error('Local WebSocket connection failed. Check that the sample server is running.'));
  ws.onclose = () => {
    if (call !== session) return;
    stop('Session closed', 'The service ended the session. You can start a new one.');
    if (session.live && !session.closed) showError('GPT Live disconnected without confirming final session usage.');
  };
}
function waitForIce(peer, signal) {
  if (peer.iceGatheringState === 'complete') return Promise.resolve();
  return new Promise((resolve, reject) => {
    const finish = (error) => {
      clearTimeout(timer); peer.removeEventListener('icegatheringstatechange', changed);
      signal.removeEventListener('abort', aborted); error ? reject(error) : resolve();
    };
    const changed = () => { if (peer.iceGatheringState === 'complete') finish(); };
    const aborted = () => finish(new Error('Session cancelled.'));
    const timer = setTimeout(() => finish(new Error('ICE gathering timed out. Check your STUN/TURN configuration.')), 12000);
    peer.addEventListener('icegatheringstatechange', changed); signal.addEventListener('abort', aborted);
    if (signal.aborted) aborted();
  });
}
async function connectWebRtc(session) {
  let offer, sent = false;
  const sendOffer = ws => {
    if (call !== session || !offer || sent || ws.readyState !== WebSocket.OPEN) return;
    sent = true;
    ws.send(JSON.stringify({type:'rtc.call.sdp.create', sdp_offer:offer}));
  };
  // Authenticate and connect to Azure while the browser gathers ICE candidates.
  openSocket(session, sendOffer);
  state('Gathering ICE candidates', 'Preparing direct audio while connecting to Azure.', 'Connecting');
  await session.peer.setLocalDescription(await session.peer.createOffer());
  await waitForIce(session.peer, session.abort.signal);
  if (call !== session) return;
  offer = session.peer.localDescription.sdp;
  state('Connecting direct audio', 'Exchanging the SDP offer and establishing the media connection.', 'Connecting');
  sendOffer(session.ws);
}
async function handleEvent(session, event) {
  if (call !== session) return;
  const type = event.type || '';
  if (type.startsWith('session.')) {
    const id = event.session?.id ?? event.session_id;
    if (typeof id === 'string' && id.trim()) {
      session.sessionId = id;
      showSessionReference(session);
    }
  }
  if (type === 'session.done' || type === 'session.closed') {
    if (event.estimated_cost !== undefined) session.returnedCost = event.estimated_cost;
    session.endedAt ??= Date.now();
    if (type === 'session.closed') {
      session.closed = true;
      stop('Conversation ended', session.live ?
        `GPT Live final cumulative voice usage: ${event.usage?.seconds ?? 'unavailable'} seconds.` :
        'Final session usage received. See the API-returned cost below.');
    }
    return;
  }
  if (session.live && type.startsWith('session.')) {
    if (type === 'session.input_transcript.delta' || type === 'session.output_transcript.delta') {
      const role = type === 'session.input_transcript.delta' ? 'user' : 'assistant';
      let record = session.liveCaption;
      if (!record || record.role !== role || event.start_ms - record.end > 1000) {
        record = session.liveCaption = {role, text:'', status:'caption'};
      }
      record.text += event.delta || '';
      record.end = event.end_ms;
      transcript(record);
    } else if (type === 'session.output_audio.delta' && session.audio && !session.closing) {
      session.audio.play(event.delta);
      if (session.audio.context.state === 'suspended') $('play').hidden = false;
    }
    return;
  }
  if (session.live && type === 'response.event') {
    const backend = event.event || {};
    if (backend.type === 'response.created') {
      session.responding = true;
      session.liveResponseParts = new Map();
    } else if (['response.output_text.delta', 'response.output_text.done'].includes(backend.type)) {
      session.liveResponseParts ||= new Map();
      const key = `${backend.item_id || backend.output_index || 0}:${backend.content_index || 0}`;
      let record = session.liveResponseParts.get(key);
      if (!record) {
        record = {role:'assistant', text:'', status:'backend'};
        session.liveResponseParts.set(key, record);
      }
      record.text = backend.type === 'response.output_text.done' ? backend.text || '' : record.text + (backend.delta || '');
      transcript(record);
    }
    const failed = ['error', 'response.failed', 'response.incomplete'].includes(backend.type);
    if (failed || backend.type === 'response.completed') {
      session.responding = false;
      for (const record of session.liveResponseParts?.values() || []) {
        record.status = failed ? 'backendIncomplete' : 'backendComplete';
        transcript(record);
      }
      session.liveResponseParts = null;
    }
    if (failed) {
      showError('The GPT Live reasoning backend failed. Check its deployment and web search configuration.');
    }
    updateTextInput();
    return;
  }
  const historyHandled = session.history.handle(event);
  if (type === 'bridge.conversation') {
    if (typeof event.downloadToken !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(event.conversationId)) {
      throw new Error('Invalid conversation download reference.');
    }
    latestDownload = {downloadToken:event.downloadToken, conversationId:event.conversationId,
      agentSelection:session.agentSelection ?? agentSelection};
    $('download-status').textContent = 'Audio is being saved in Azure. End the conversation to download.';
    updateAgentControls();
  } else if (type === 'session.updated' && session.transport === 'websocket') {
    validateAudioSession(event.session);
  } else if (type === 'bridge.ready') {
    session.configured = true;
    markReady(session);
  } else if (type === 'response.created') {
    session.responseId = event.response?.id;
    session.responding = true;
    updateTextInput();
  } else if (type === 'response.done') {
    session.responding = false;
    updateTextInput();
    if (event.response?.status === 'failed') {
      showError(event.response.status_details?.error?.message || 'The agent could not respond. Try sending your message again.');
    }
  } else if (type === 'rtc.call.sdp.created') {
    if (!event.sdp_answer) throw new Error('Azure returned no SDP answer.');
    await session.peer.setRemoteDescription({type:'answer', sdp:event.sdp_answer});
  } else if (['bridge.error','rtc.call.error','error'].includes(type)) {
    if (historyHandled) return;
    throw new Error(event.error?.message || 'Azure could not start this voice session.');
  } else if (type === 'input_audio_buffer.speech_started') {
    session.history.reserveUser(event.item_id, 'Listening...');
    session.userSpeaking = true;
    updateTextInput();
    session.interruptedResponse = session.responseId;
    session.audio?.interrupt();
    $('orb').className = 'orb active'; $('status').textContent = 'Listening to you';
  } else if (type === 'input_audio_buffer.speech_stopped') {
    session.history.reserveUser(event.item_id);
    session.userSpeaking = false;
    updateTextInput();
  } else if (type === 'input_audio_buffer.committed') {
    session.history.reserveUser(event.item_id);
  } else if (['conversation.item.created', 'conversation.item.added'].includes(type) && event.item?.role === 'user') {
    const text = Array.isArray(event.item.content) ? event.item.content.map(part => part.text || part.transcript || '').join('') : '';
    if (text) session.history.user(event.item.id, text);
    else session.history.reserveUser(event.item.id);
  } else if (['response.audio.delta', 'response.output_audio.delta'].includes(type) && session.audio && !session.closing) {
    const responseId = event.response_id || session.responseId;
    if (event.delta && !session.userSpeaking && (!session.interruptedResponse || responseId !== session.interruptedResponse)) {
      session.audio.play(event.delta);
      if (session.audio.context.state === 'suspended') $('play').hidden = false;
    }
  } else if (type === 'output_audio_buffer.started') {
    $('orb').className = 'orb speaking'; $('status').textContent = 'Your agent is speaking';
  } else if (type === 'output_audio_buffer.stopped' || type === 'output_audio_buffer.cleared') {
    $('orb').className = 'orb active'; $('status').textContent = 'Listening';
  } else if (type === 'conversation.item.input_audio_transcription.delta') {
    session.history.user(event.item_id, event.delta, true);
  } else if (type === 'conversation.item.input_audio_transcription.completed') {
    session.history.user(event.item_id, event.transcript || 'No speech recognized.');
  } else if (type === 'conversation.item.input_audio_transcription.failed') {
    session.history.user(event.item_id, 'Speech transcription failed.');
    showError('Speech transcription failed for this turn. Check the transcription setting in Foundry.');
  }
}
async function start() {
  if (call || preparingAgent || !config?.configured) return;
  $('session-reference').hidden = true;
  $('session-reference').textContent = '';
  try {
    await prepareAgent();
  } catch (error) {
    showSessionReference({});
    showError(error.message);
    return;
  }
  const personalization = $('personalize').checked ? {
    type:'bridge.personalize', display_name:$('display-name').value,
    user_context:$('user-context').value, client_language:$('client-language').value,
    english_only:$('english-only').checked,
  } : null;
  const session = {abort:new AbortController(), muted:false, transport:$('transport').value, ready:false, personalization,
    model:savedModel,
    live:savedModel === 'gpt-live-1', agentSelection,
    voice:savedModel === 'gpt-live-1' ? '' :
      savedModel === 'azure-realtime' ? $('native-voice').value : $('voice').value}; call = session;
  session.history = createHistory(session);
  $('personalization').disabled = true;
  $('voice').disabled = true;
  $('native-voice').disabled = true;
  updateAgentControls();
  $('error').hidden = true; $('start').disabled = true; $('stop').disabled = false;
  $('transport').disabled = true; $('elapsed').textContent = '00:00';
  state('Let’s connect', 'Allow microphone access when your browser asks.', 'Connecting');
  session.timeout = setTimeout(() => fail(session, new Error('Connection timed out. Check microphone permission, outbound HTTPS access and your selected transport.')), 60000);
  try {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia)
      throw new Error('Use a microphone-capable browser on localhost or HTTPS.');
    if (session.transport === 'websocket') {
      session.audio = new PcmAudio(playing => {
        if (call !== session) return;
        $('orb').className = playing ? 'orb speaking' : 'orb active';
        $('status').textContent = playing ? 'Your agent is speaking' : 'Listening';
      });
      await session.audio.resumePromise;
      if (call !== session) return;
    } else if (!window.RTCPeerConnection) throw new Error('This browser does not support WebRTC. Choose WebSocket.');
    const mic = await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true}});
    if (call !== session) { mic.getTracks().forEach(t => t.stop()); return; }
    session.mic = mic;
    mic.getTracks().forEach(t => {
      t.enabled = false;
      t.onended = () => fail(session, new Error('Microphone disconnected.'));
    });
    if (session.transport === 'websocket') {
      await session.audio.prepare(mic, audio => {
        if (call !== session || !session.ready || session.ws?.readyState !== WebSocket.OPEN) return;
        if (session.ws.bufferedAmount > 256 * 1024) {
          fail(session, new Error('Audio upload cannot keep up. Check the network and start a new session.'));
          return;
        }
        session.ws.send(JSON.stringify({type:session.live ? 'session.input_audio.append' : 'input_audio_buffer.append', audio}));
      });
      if (call !== session) return;
      state('Connecting to Azure', 'Opening an audio WebSocket. No STUN or TURN needed.', 'Connecting');
      openSocket(session);
      return;
    }
    session.remote = new MediaStream();
    const peer = session.peer = new RTCPeerConnection({iceServers:config.iceServers});
    mic.getTracks().forEach(t => peer.addTrack(t, mic));
    peer.ontrack = e => { if (call === session) { session.remote.addTrack(e.track); $('remote-audio').srcObject = session.remote; playAudio(); } };
    peer.onconnectionstatechange = () => {
      if (call !== session) return;
      if (peer.connectionState === 'connected') {
        markReady(session);
      } else if (peer.connectionState === 'failed') fail(session, new Error('WebRTC media failed. Check firewall rules or configure TURN.'));
      else if (peer.connectionState === 'disconnected') {
        showSessionReference(session);
        session.ready = false;
        session.mic?.getAudioTracks().forEach(t => { t.enabled = false; });
        updateTextInput();
        state('Reconnecting', 'The media connection was interrupted.', 'Reconnecting');
        clearTimeout(session.disconnectTimer);
        session.disconnectTimer = setTimeout(() => fail(session, new Error('Media connection lost. Start a new session.')),10000);
      }
    };
    // Required in the offer. Events use the signaling socket to avoid duplicate transcripts.
    session.channel = peer.createDataChannel('voice-live-events');
    await connectWebRtc(session);
  } catch (error) {
    if (error.name === 'NotAllowedError') error = new Error('Microphone access was denied. Allow it in your browser’s site permissions and try again.');
    fail(session,error);
  }
}
$('start').onclick = start;
$('agent-model-choice').onchange = () => {
  updateVoiceChoices($('agent-model-choice').value);
  updateAgentControls();
};
$('agent-transcription-choice').onchange = updateAgentControls;
$('save-model').onclick = saveAgentModel;
$('show-settings').onclick = showAgentSettings;
$('close-settings').onclick = () => $('settings-dialog').close();
$('download-conversation').onclick = downloadConversation;
$('recreate-agent').onclick = async () => {
  try { await prepareAgent(true); } catch (error) { showError(error.message); }
};
if (typeof navigator !== 'undefined') $('client-language').value = navigator.language?.toLowerCase().startsWith('es') ? 'es-ES' : 'en-US';
$('text-form').onsubmit = sendText;
$('text-input').oninput = updateTextInput;
$('stop').onclick = endCall;
$('play').onclick = playAudio;
$('mute').onclick = () => {
  if (!call?.mic) return;
  call.muted = !call.muted; call.mic.getAudioTracks().forEach(t => { t.enabled = call.ready && !call.muted; });
  call.audio?.mute(call.muted);
  $('mute').textContent = call.muted ? 'Unmute mic' : 'Mute mic';
  $('mute').setAttribute('aria-pressed', String(call.muted));
  $('hint').textContent = call.muted ? 'Your microphone is muted. You can still type and hear the agent.' :
    call.live ? 'Speak or type. GPT Live can listen and speak at the same time.' : 'Say hello or send a text message. Your agent will respond.';
};
$('clear').onclick = () => { call?.history.clear(); if (call) call.liveCaption = null; messages.clear(); $('transcript').replaceChildren(); };
window.addEventListener('pagehide', () => stop());
$('transport').onchange = () => {
  $('media').textContent = $('transport').value === 'websocket' ? 'WebSocket · PCM16 / 24 kHz' : 'WebRTC · Opus';
};
try {
  const response = await fetch(agentUrl('/api/config')); if (!response.ok) throw new Error('Could not load server configuration.');
  config = await response.json(); $('agent').textContent = config.agent || 'Not configured';
  $('advanced-agent').hidden = !config.agentRecreateAllowed;
  if (config.personalizationRequired) {
    $('personalize').checked = true;
    $('personalize').disabled = true;
  }
  $('transport').value = config.defaultTransport || 'websocket'; $('transport').onchange();
  updateAgentControls();
  if (!config.configured) {
    $('setup').open = true;
    state('One small setup first', 'Set your project endpoint in .env, then sign in with az login. No Agent is needed yet.', 'Setup needed');
    showError((config.setupIssues || ['Complete the server .env settings.']).join(' '));
  } else if (config.authentication === 'azure_cli' && !config.azureCliInstalled) {
    $('setup').open = true;
    showError('Install Azure CLI, then run az login in a normal PowerShell window. No API key or client secret is needed.');
  } else {
    await inspectAgent();
  }
} catch (error) { showError(error.message); }

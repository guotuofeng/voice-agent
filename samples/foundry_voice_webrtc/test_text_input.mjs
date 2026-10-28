import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';
import { RetainedHistory } from './public/retained-history.mjs';
import { returnedCallCost } from './public/call-cost.mjs';

const source = await readFile(new URL('./public/app.js', import.meta.url), 'utf8');
const sessionCost = {amount:0.006344, currency:'USD', status:'complete',
  price_version:'azure-retail-prices-eastus2-2026-06-01', unpriced_components:[]};

async function setup(transport, overrides = {}) {
  const elements = new Map();
  const created = [];
  const makeElement = () => ({
    value: '', disabled: true, hidden: true, textContent: '', children: [],
    append(...children) { this.children.push(...children); children.forEach(child => { child.parent = this; }); },
    replaceChildren(...children) { this.children = children; },
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); },
    setAttribute() {}, pause() {}, click() { this.clicked = true; },
    showModal() { this.open = true; }, close() { this.open = false; },
  });
  const element = id => {
    assert.notEqual(id, 'prepare-agent', 'The removed setup button must not be referenced');
    assert.notEqual(id, 'live-voice', 'The removed Live voice selector must not be referenced');
    if (!elements.has(id)) elements.set(id, makeElement());
    return elements.get(id);
  };
  const sent = [];
  const session = {
    transport, ready: false, ws: { readyState: 1, send: frame => sent.push(JSON.parse(frame)), close() {} },
    peer: transport === 'webrtc' ? {connectionState:'connected', close() {}} : undefined,
    abort: new AbortController(),
  };
  const context = {
    document: { getElementById: element, createElement: tag => {
      const item = makeElement(); item.tag = tag; created.push(item); return item;
    }, body:makeElement() },
    window: { addEventListener() {} }, WebSocket: { OPEN: 1 },
    crypto: webcrypto,
    sessionStorage: {getItem: () => null, setItem() {}},
    fetch: async () => ({ ok: true, json: async () => ({
      configured: true, agent:'demo-agent', exists:true, transcriptionModel:'mai-transcribe-2',
    }) }),
    clearTimeout() {}, clearInterval() {}, setInterval: () => 1, setTimeout: () => 1, AbortController, AbortSignal,
    validateAudioSession() {},
    RetainedHistory,
    returnedCallCost,
    session,
    ...overrides,
  };
  // Run the actual UI code with a minimal DOM, without browser audio imports.
  await vm.runInNewContext(`(async () => {
    ${source.replace(/^import .*;\r?\n/gm, '')}
    call = session;
    session.history = createHistory(session);
    return {markReady, handleEvent, updateTextInput, stop, connectWebRtc, start, openSocket, prepareAgent,
      applyAgentDetails, inspectAgent,
      reconnect: () => { call = session; session.history = createHistory(session); }};
  })()`, context).then(api => context.api = api);
  return { ...context.api, element, session, sent, created };
}

for (const transport of ['websocket', 'webrtc']) {
  test(`${transport}: failed responses display the Azure session ID and retain it after stopping`, async () => {
    const ui = await setup(transport);
    await ui.handleEvent(ui.session, {type:'session.created', session:{id:'sess_failure'}});
    await ui.handleEvent(ui.session, {type:'response.done', response:{
      status:'failed', status_details:{error:{message:'Unknown error'}},
    }});
    assert.equal(ui.element('error').textContent, 'Unknown error');
    assert.equal(ui.element('session-reference').textContent, 'Session ID: sess_failure');
    assert.equal(ui.element('session-reference').hidden, false);
    ui.stop();
    assert.equal(ui.element('session-reference').textContent, 'Session ID: sess_failure');
    ui.stop();
    assert.equal(ui.element('session-reference').textContent, 'Session ID: sess_failure');
    await ui.start();
    assert.doesNotMatch(ui.element('session-reference').textContent, /sess_failure/);
  });

  test(`${transport}: socket errors and disconnects preserve session diagnostics`, async () => {
    for (const callback of ['onerror', 'onclose']) {
      let socket;
      class Socket {
        static OPEN = 1;
        constructor() { this.readyState = 1; socket = this; }
        close() {}
      }
      const ui = await setup(transport, {WebSocket:Socket, location:{protocol:'https:',host:'example.test'}});
      ui.openSocket(ui.session);
      await ui.handleEvent(ui.session, {type:'session.updated', session:{id:'sess_disconnected'}});
      socket[callback]();
      assert.equal(ui.element('session-reference').textContent, 'Session ID: sess_disconnected');
      assert.equal(ui.element('session-reference').hidden, false);
    }
  });

  test(`${transport}: failure before Azure startup reports no session ID rather than inventing one`, async () => {
    let socket;
    class Socket {
      static OPEN = 1;
      constructor() { this.readyState = 1; socket = this; }
      close() {}
    }
    const ui = await setup(transport, {WebSocket:Socket, location:{protocol:'https:',host:'example.test'}});
    ui.openSocket(ui.session);
    socket.onerror();
    assert.equal(ui.element('session-reference').textContent, 'Session ID: unavailable (Azure did not return one).');
    assert.equal(ui.element('session-reference').hidden, false);
  });

  test(`${transport}: Flash warns that automatic truncation and authoritative retained history are disabled`, async () => {
    const ui = await setup(transport, {
      fetch: async url => ({ok:true, json:async()=>url === '/api/config' ?
        {configured:true} : {exists:true, model:'azure-realtime-flash-2026-10-14', version:'1'}}),
    });
    assert.match(ui.element('history-help').textContent, /Automatic interruption truncation is disabled/);
    assert.match(ui.element('history-help').textContent, /not authoritative retained history/);
    ui.session.configured = true;
    ui.markReady(ui.session);
    assert.equal(ui.session.ready, true);
    assert.equal(ui.element('text-input').disabled, false);
  });

  test(`${transport}: graceful close displays API cost exactly once and excludes setup/close wait`, async () => {
    let now = 1000;
    const ui = await setup(transport, {
      Date: {now:()=>now},
      fetch: async url => ({ok:true, json:async()=>url === '/api/config' ?
        {configured:true} : {exists:true, model:'gpt-5.4', version:'1'}}),
    });
    now = 11000;
    ui.session.configured = true;
    ui.markReady(ui.session);
    assert.equal(ui.element('call-cost').hidden, true);
    now = 41000;
    await ui.handleEvent(ui.session, {type:'response.done', response:{estimated_cost:{...sessionCost, amount:5}}});
    await ui.element('stop').onclick();
    assert.equal(ui.sent.at(-1).type, 'session.close');
    assert.equal(ui.element('call-cost').hidden, true);
    await ui.element('stop').onclick();
    assert.equal(ui.sent.filter(frame=>frame.type==='session.close').length, 1);
    now = 45000;
    await ui.handleEvent(ui.session, {type:'session.done', estimated_cost:sessionCost});
    await ui.handleEvent(ui.session, {type:'session.closed', estimated_cost:sessionCost});
    assert.equal(ui.element('call-cost').hidden, false);
    assert.equal(ui.element('call-cost-total').textContent, '$0.006344 USD');
    assert.equal(ui.element('call-cost-minute').textContent, '$0.012688 USD/min');
    assert.match(ui.element('call-cost-note').textContent, /30.0 connected seconds/);
    assert.match(ui.element('call-cost-note').textContent, /Complete API estimate/);
    assert.match(ui.element('call-cost-details').textContent, /azure-retail-prices-eastus2-2026-06-01/);
    assert.match(ui.element('call-cost-details').textContent, /No assumed token counts/);
    const total = ui.element('call-cost-total').textContent;
    now = 90000;
    ui.stop();
    assert.equal(ui.element('call-cost-total').textContent, total);
  });
}

test('GPT Live retains its session ID after backend errors and session closure', async () => {
  const ui = await setup('websocket');
  ui.session.live = true;
  await ui.handleEvent(ui.session, {type:'session.started', session_id:'live_session'});
  await ui.handleEvent(ui.session, {type:'response.event', event:{type:'response.failed'}});
  assert.equal(ui.element('session-reference').textContent, 'Session ID: live_session');
  assert.match(ui.element('error').textContent, /reasoning backend failed/);
  await ui.handleEvent(ui.session, {type:'session.closed', usage:{seconds:0}});
  assert.equal(ui.element('session-reference').textContent, 'Session ID: live_session');
  assert.equal(ui.element('session-reference').hidden, false);
});

test('Agent preparation failure clears the previous session ID and reports it as unavailable', async () => {
  const ui = await setup('websocket', {
    fetch: async url => ({ok:url !== '/api/agent/prepare', json:async()=>url === '/api/config' ?
      {configured:true} : url === '/api/agent/prepare' ? {error:'Agent preparation failed'} :
      {exists:true, model:'gpt-5.4', version:'1'}}),
  });
  await ui.handleEvent(ui.session, {type:'session.created', session:{id:'sess_previous'}});
  ui.stop();
  await ui.start();
  assert.equal(ui.element('error').textContent, 'Agent preparation failed');
  assert.equal(ui.element('session-reference').textContent, 'Session ID: unavailable (Azure did not return one).');
  assert.equal(ui.element('session-reference').hidden, false);
});

test('Live cost excludes close-acknowledgment wait and retains final usage display', async () => {
  let now = 1000;
  const ui = await setup('websocket', {
    Date: {now:()=>now},
    fetch: async url => ({ok:true, json:async()=>url === '/api/config' ?
      {configured:true} :
      {exists:true, model:'gpt-live-1-test', modelChoice:'gpt-live-1', version:'1'}}),
  });
  ui.session.live = true;
  ui.session.configured = true;
  ui.markReady(ui.session);
  now = 61000;
  await ui.element('stop').onclick();
  now = 70000;
  await ui.handleEvent(ui.session, {type:'session.closed', usage:{seconds:62}, estimated_cost:sessionCost});
  assert.equal(ui.element('call-cost-total').textContent, '$0.006344 USD');
  assert.match(ui.element('call-cost-note').textContent, /60.0 connected seconds/);
  assert.match(ui.element('hint').textContent, /62 seconds/);
});

test('disconnects and duration-only Live usage show unavailable, not assumed prices', async () => {
  for (const model of ['azure-realtime-flash-2026-10-14', 'gpt-live-1']) {
    let now = 1000;
    const ui = await setup('websocket', {
      Date: {now:()=>now},
      fetch: async url => ({ok:true, json:async()=>url === '/api/config' ?
        {configured:true} : {exists:true, model, version:'1'}}),
    });
    ui.session.configured = true;
    ui.markReady(ui.session);
    now = 61000;
    if (model === 'gpt-live-1') {
      ui.session.live = true;
      await ui.handleEvent(ui.session, {type:'session.closed', usage:{seconds:60}});
    } else {
      ui.stop();
    }
    assert.equal(ui.element('call-cost-total').textContent, 'Unavailable');
    assert.equal(ui.element('call-cost-minute').textContent, 'Unavailable');
    assert.match(ui.element('call-cost-note').textContent, /did not return a finalized session cost/);
  }
});

test('partial API costs are labeled as subtotals instead of complete totals', async () => {
  const ui = await setup('websocket');
  ui.session.configured = true;
  ui.markReady(ui.session);
  ui.session.beganAt = Date.now() - 60000;
  await ui.handleEvent(ui.session, {type:'session.closed',
    estimated_cost:{...sessionCost, status:'partial', unpriced_components:['byom_model']}});
  assert.equal(ui.element('call-cost-total-label').textContent, 'Priced subtotal (partial)');
  assert.equal(ui.element('call-cost-total').textContent, '$0.006344 USD');
  assert.match(ui.element('call-cost-minute').textContent, /\(partial\)/);
  assert.match(ui.element('call-cost-note').textContent, /Partial/);
  assert.match(ui.element('call-cost-details').textContent, /byom_model/);
});

test('failed connection without readiness does not show a call-cost estimate', async () => {
  const ui = await setup('websocket');
  ui.stop();
  assert.equal(ui.element('call-cost').hidden, true);
});

test('Agent creation UI reports creation and enables required personalization', async () => {
  const requests = [];
  const ui = await setup('websocket', {
    fetch: async (url, options) => {
      requests.push([url, options?.method]);
      return {ok:true, json:async()=>url === '/api/config' ? {configured:true} :
        url === '/api/agent' ? {exists:true} :
        {created:true, personalizationRequired:true, weatherEnabled:true}};
    },
  });

  ui.stop();
  await ui.prepareAgent();
  assert.equal(ui.element('agent-preparation').textContent, 'Your Agent is saved and ready.');
  assert.equal(ui.element('personalize').checked, true);
  assert.equal(ui.element('personalize').disabled, true);
  assert.equal(ui.element('weather-tool-hint').hidden, false);
  assert.deepEqual(requests[2], ['/api/agent/prepare', 'POST']);
});

for (const model of ['azure-realtime-flash-2026-10-14', 'gpt-realtime-2.1-mini']) {
  test(`${model} saves the exact model ID and uses standard voices`, async () => {
    const writes = [];
    const ui = await setup('websocket', {
      fetch: async (url, options) => {
        if (options?.body) writes.push(JSON.parse(options.body));
        return {ok:true, json:async()=>url === '/api/config' ? {configured:true} :
          {exists:true, model:options?.method === 'POST' ? model : 'azure-realtime',
            version:options?.method === 'POST' ? '2' : '1', transcriptionModel:'mai-transcribe-2'}};
      },
    });
    ui.stop();
    ui.element('agent-model-choice').value = model;
    ui.element('agent-model-choice').onchange();
    assert.equal(ui.element('voice').hidden, false);
    assert.equal(ui.element('native-voice').hidden, true);
    assert.equal(ui.element('live-voice-info').hidden, true);
    assert.equal(ui.element('agent-transcription-choice').disabled, false);
    assert.equal(ui.element('save-model').disabled, false);
    await ui.element('save-model').onclick();
    assert.deepEqual(writes, [{model, version:'1', transcriptionModel:'mai-transcribe-2'}]);
    assert.equal(ui.element('agent-model-choice').value, model);
    assert.equal(ui.element('agent-model').textContent, model);
    assert.equal(ui.element('voice').hidden, false);
    assert.equal(ui.element('native-voice').hidden, true);
  });
}

test('GPT Live selection uses the companion endpoint and returning to realtime preserves its saved model', async () => {
  const requests = [];
  const ui = await setup('websocket', {
    fetch: async (url, options) => {
      if (options?.body) requests.push([url, JSON.parse(options.body)]);
      return {ok:true, json:async()=>url === '/api/config' ? {configured:true, defaultTranscriptionModel:'mai-transcribe-2'} :
        options?.method === 'POST' && JSON.parse(options.body).model === 'gpt-live-1' ?
          {agent:'demo-agent-live', agentSelection:'live', model:'gpt-live-1-test', modelChoice:'gpt-live-1', version:'1', transcriptionModel:null} :
          {exists:true, agentSelection:'base', model:'gpt-5.4', version:'1', transcriptionModel:'mai-transcribe-2'}};
    },
  });
  ui.stop();
  ui.element('agent-model-choice').value = 'gpt-live-1';
  ui.element('agent-model-choice').onchange();
  assert.equal(ui.element('live-voice-info').hidden, false);
  assert.equal(ui.element('voice-label').hidden, true);
  assert.equal(ui.element('native-voice').hidden, true);
  assert.equal(ui.element('voice').hidden, true);
  assert.equal(ui.element('agent-transcription-choice').disabled, true);
  assert.equal(ui.element('save-model').disabled, false);
  assert.equal(ui.element('save-model').textContent, 'Create or use GPT Live Agent');
  assert.equal(ui.element('start').disabled, true);
  assert.equal(ui.element('transport').disabled, true);
  await ui.element('save-model').onclick();
  assert.deepEqual(requests, [['/api/agent/switch', {model:'gpt-live-1', version:'1'}]]);
  assert.equal(ui.element('agent').textContent, 'demo-agent-live');
  assert.equal(ui.element('agent-model').textContent, 'gpt-live-1-test');
  assert.equal(ui.element('agent-model-choice').value, 'gpt-live-1');
  assert.equal(ui.element('agent-transcription').textContent, 'Built into GPT Live');
  assert.equal(ui.element('transport').value, 'websocket');
  assert.equal(ui.element('transport').disabled, true);
  ui.element('agent-model-choice').value = 'gpt-5.4';
  ui.element('agent-model-choice').onchange();
  assert.equal(ui.element('live-voice-info').hidden, true);
  assert.equal(ui.element('voice-label').hidden, false);
  assert.equal(ui.element('voice').hidden, false);
  assert.equal(ui.element('agent-transcription-choice').disabled, true);
  assert.equal(ui.element('save-model').textContent, 'Use realtime Agent');
  await ui.element('save-model').onclick();
  assert.deepEqual(requests[1], ['/api/agent/switch?agent_selection=live', {model:'gpt-5.4', version:'1'}]);
  assert.equal(ui.element('agent-model').textContent, 'gpt-5.4');
  assert.equal(ui.element('save-model').textContent, 'Save new version');
  assert.equal(ui.element('agent-transcription-choice').disabled, false);
});

test('Tabs retain independent selections across reloads and route API requests and sockets explicitly', async () => {
  const firstStorage = new Map(), secondStorage = new Map();
  const storage = values => ({getItem:key=>values.get(key), setItem:(key,value)=>values.set(key,value)});
  const requests = [], sockets = [];
  const options = values => ({
    sessionStorage:storage(values), location:{protocol:'http:', host:'localhost:8080'},
    WebSocket:class {static OPEN=1; constructor(url){this.readyState=1; sockets.push(url);} close(){}},
    fetch:async (url, request) => {
      requests.push(url);
      const live = url.includes('agent_selection=live') || request?.body?.includes('gpt-live-1');
      return {ok:true, json:async()=>({configured:true, exists:true, version:'1', yaml:'kind: voice',
        agent:live ? 'demo-agent-live' : 'demo-agent', agentSelection:live ? 'live' : 'base',
        model:live ? 'gpt-live-1' : 'gpt-5.4', transcriptionModel:'mai-transcribe-2'})};
    },
  });
  const first = await setup('websocket', options(firstStorage));
  const second = await setup('websocket', options(secondStorage));
  first.stop(); second.stop();
  first.element('agent-model-choice').value = 'gpt-live-1';
  await first.element('save-model').onclick();
  assert.equal(firstStorage.get('voice-agent-selection'), 'live');
  assert.equal(secondStorage.get('voice-agent-selection'), 'base');
  await second.prepareAgent();
  assert.equal(requests.at(-1), '/api/agent/prepare');
  assert.equal(second.element('agent').textContent, 'demo-agent');
  const reloaded = await setup('websocket', options(firstStorage));
  assert.deepEqual(requests.slice(-2), ['/api/config?agent_selection=live', '/api/agent?agent_selection=live']);
  reloaded.stop();
  await reloaded.prepareAgent();
  assert.equal(requests.at(-1), '/api/agent/prepare?agent_selection=live');
  await reloaded.element('show-settings').onclick();
  assert.equal(requests.at(-1), '/api/agent/settings?agent_selection=live');
  reloaded.session.agentSelection = 'live';
  reloaded.session.live = true;
  reloaded.openSocket(reloaded.session);
  assert.match(sockets.at(-1), /agent_selection=live/);
  assert.match(sockets.at(-1), /protocol=live/);
  reloaded.stop();
  await second.inspectAgent();
  assert.equal(requests.at(-1), '/api/agent');
});

test('Failed switches preserve tab selection; missing companion fallback changes only that tab', async () => {
  const saved = new Map([['voice-agent-selection', 'live']]);
  const requests = [];
  let fallback = false;
  const ui = await setup('websocket', {
    sessionStorage:{getItem:key=>saved.get(key), setItem:(key,value)=>saved.set(key,value)},
    fetch:async (url, options) => {
      requests.push(url);
      return {ok:!options?.method, json:async()=>options?.method ? {error:'Switch failed'} :
        {configured:true, exists:true, version:'1', agentSelection:fallback ? 'base' : 'live',
          model:fallback ? 'gpt-5.4' : 'gpt-live-1'}};
    },
  });
  ui.stop();
  ui.element('agent-model-choice').value = 'gpt-5.4';
  await ui.element('save-model').onclick();
  assert.equal(saved.get('voice-agent-selection'), 'live');
  assert.match(ui.element('error').textContent, /Switch failed/);
  fallback = true;
  await ui.inspectAgent();
  assert.equal(saved.get('voice-agent-selection'), 'base');
  await ui.element('show-settings').onclick();
  assert.equal(requests.at(-1), '/api/agent/settings');
});

for (const transport of ['websocket', 'webrtc']) {
  test(`${transport}: GPT Live captions and audio bypass realtime retained-history and turn-taking`, async () => {
    const ui = await setup(transport);
    ui.session.live = true;
    ui.session.userSpeaking = true;
    ui.session.configured = true;
    const played = [];
    ui.session.audio = {play:delta=>played.push(delta), context:{state:'running'}, mute() {}, dispose() {}};
    ui.markReady(ui.session);
    await ui.handleEvent(ui.session, {type:'session.output_audio.delta', delta:'AAA='});
    assert.deepEqual(played, ['AAA=']);
    await ui.handleEvent(ui.session, {type:'session.input_transcript.delta', delta:'Hi', start_ms:0, end_ms:500});
    await ui.handleEvent(ui.session, {type:'session.output_transcript.delta', delta:'Hello', start_ms:200, end_ms:600});
    const captions = ui.element('transcript').children;
    assert.equal(captions.length, 2);
    assert.equal(captions[1].children[0].textContent, 'Agent - live caption (not retained history)');
    assert.equal(captions[1].children[1].textContent, 'Hello');
    ui.updateTextInput();
    assert.equal(ui.element('text-input').disabled, false);
    assert.equal(ui.element('send-text').disabled, true);
    await ui.element('stop').onclick();
    assert.equal(ui.sent[0].type, 'session.close');
    assert.equal(ui.element('stop').disabled, true);
    await ui.handleEvent(ui.session, {type:'session.closed', usage:{seconds:4}});
    assert.equal(ui.element('status').textContent, 'Conversation ended');
    assert.match(ui.element('hint').textContent, /4 seconds/);
  });
}

test('GPT Live sends native user text and renders delegated replies without realtime history', async () => {
  const ui = await setup('websocket');
  ui.session.live = true;
  ui.session.configured = true;
  ui.session.userSpeaking = true;
  ui.session.history = {handle() { throw new Error('Live text must bypass realtime history'); }, dispose() {}};
  ui.markReady(ui.session);
  ui.element('text-input').value = 'Find the latest Azure news';
  ui.updateTextInput();
  assert.equal(ui.element('send-text').disabled, false);
  ui.element('text-form').onsubmit({preventDefault() {}});
  assert.deepEqual(ui.sent, [
    {type:'response.item.create', item:{type:'message', role:'user',
      content:[{type:'input_text', text:'Find the latest Azure news'}]}},
    {type:'response.create'},
  ]);
  assert.equal(ui.element('transcript').children[0].children[1].textContent, 'Find the latest Azure news');
  assert.equal(ui.session.responding, true);
  ui.element('text-input').value = 'Next question';
  ui.element('text-form').onsubmit({preventDefault() {}});
  assert.equal(ui.sent.length, 2);
  for (const event of [
    {type:'response.created', response:{id:'r1'}},
    {type:'response.output_text.delta', item_id:'m1', content_index:0, delta:'Azure '},
    {type:'response.output_text.delta', item_id:'m1', content_index:0, delta:'news'},
    {type:'response.output_text.done', item_id:'m1', content_index:0, text:'Azure news.'},
    {type:'response.completed', response:{id:'r1'}},
  ]) await ui.handleEvent(ui.session, {type:'response.event', event});
  const reply = ui.element('transcript').children[1];
  assert.equal(reply.children[0].textContent, 'Agent - Responses backend');
  assert.equal(reply.children[1].textContent, 'Azure news.');
  assert.equal(ui.session.responding, false);
  assert.equal(ui.element('send-text').disabled, false);
});

for (const failure of ['error', 'response.failed', 'response.incomplete']) {
  test(`GPT Live ${failure} unlocks text input and marks partial replies incomplete`, async () => {
    const ui = await setup('websocket');
    ui.session.live = true;
    ui.session.configured = true;
    ui.markReady(ui.session);
    await ui.handleEvent(ui.session, {type:'response.event', event:{type:'response.created'}});
    await ui.handleEvent(ui.session, {type:'response.event', event:{type:'response.output_text.delta', delta:'Partial'}});
    ui.element('text-input').value = 'Try again';
    await ui.handleEvent(ui.session, {type:'response.event', event:{type:failure}});
    assert.equal(ui.session.responding, false);
    assert.equal(ui.element('send-text').disabled, false);
    assert.equal(ui.element('error').hidden, false);
    assert.equal(ui.element('transcript').children[0].children[0].textContent, 'Agent - Responses backend (incomplete)');
  });
}

test('Agent preparation failure is visible and never claims success', async () => {
  const ui = await setup('websocket', {
    fetch: async url => ({ok:url !== '/api/agent/prepare', json:async()=>url === '/api/config' ?
      {configured:true} : url === '/api/agent' ? {exists:true} : {error:'Creation denied'}}),
  });
  ui.stop();
  await assert.rejects(ui.prepareAgent(), /Creation denied/);
  assert.equal(ui.element('agent-preparation').textContent, 'Agent preparation failed.');
  assert.equal(ui.element('agent-model').textContent, 'Unavailable');
});

test('Model display uses the saved Agent model, not a hardcoded default', async () => {
  const ui = await setup('websocket', {
    fetch: async url => ({ok:true, json:async()=>url === '/api/config' ? {configured:true} :
      {exists:true, model:'custom-saved-model'}}),
  });
  assert.equal(ui.element('agent-model').textContent, 'custom-saved-model');
});

test('Planned GPT Realtime model becomes the saved model after setup', async () => {
  const ui = await setup('websocket', {
    fetch: async url => ({ok:true, json:async()=>url === '/api/config' ? {configured:true} :
      url === '/api/agent' ? {exists:false, model:'gpt-realtime-2.1'} : {created:true, model:'gpt-realtime-2.1', version:'1'}}),
  });
  assert.equal(ui.element('agent-model').textContent, 'gpt-realtime-2.1 (after setup)');
  assert.equal(ui.element('agent-version').textContent, 'Not created');
  ui.stop();
  await ui.prepareAgent();
  assert.equal(ui.element('agent-model').textContent, 'gpt-realtime-2.1');
  assert.equal(ui.element('agent-version').textContent, '1');
});

test('Choosing Azure Realtime previews native voices; saving updates actual model and version', async () => {
  const writes = [];
  const ui = await setup('websocket', {fetch:async (url, options) => {
    if (url === '/api/agent/model') writes.push(JSON.parse(options.body));
    return {ok:true, json:async()=>url === '/api/config' ? {configured:true} :
      {exists:true, model:url === '/api/agent/model' ? 'azure-realtime' : 'gpt-realtime-2.1',
        transcriptionModel:'mai-transcribe-2',
        version:url === '/api/agent/model' ? '2' : '1'}};
  }});
  ui.stop();
  ui.element('agent-model-choice').value = 'azure-realtime';
  ui.element('agent-model-choice').onchange();
  assert.equal(ui.element('voice').hidden, true);
  assert.equal(ui.element('native-voice').hidden, false);
  assert.equal(ui.element('agent-model').textContent, 'gpt-realtime-2.1');
  assert.equal(ui.element('agent-version').textContent, '1');
  await ui.element('save-model').onclick();
  assert.deepEqual(writes, [{model:'azure-realtime', version:'1', transcriptionModel:'mai-transcribe-2'}]);
  assert.equal(ui.element('agent-model').textContent, 'azure-realtime');
  assert.equal(ui.element('agent-version').textContent, '2');
  assert.equal(ui.element('save-model').disabled, true);
  ui.element('agent-model-choice').value = 'gpt-5.4';
  ui.element('agent-model-choice').onchange();
  assert.equal(ui.element('voice').hidden, false);
  assert.equal(ui.element('native-voice').hidden, true);
});

test('Agent settings are fetched as read-only YAML and rendered as text', async () => {
  const yaml = 'name: demo-agent\nversion: "2"\ninstructions: <script>not markup</script>';
  const ui = await setup('websocket', {fetch:async url=>({ok:true, json:async()=>url === '/api/config' ?
    {configured:true} : url === '/api/agent/settings' ? {yaml, model:'gpt-5.4', version:'2'} :
    {exists:true, model:'gpt-5.4', version:'2'}})});
  await ui.element('show-settings').onclick();
  assert.equal(ui.element('settings-yaml').textContent, yaml);
  assert.equal(ui.element('settings-dialog').open, true);
  assert.match(ui.element('settings-version').textContent, /Version: 2/);
  ui.element('close-settings').onclick();
  assert.equal(ui.element('settings-dialog').open, false);
});

test('GPT-5.4 replaces GPT-4.1 and saves a new version with HD voice controls', async () => {
  const writes = [];
  const ui = await setup('websocket', {fetch:async (url, options)=>{
    if (url === '/api/agent/model') writes.push(JSON.parse(options.body));
    return {ok:true, json:async()=>url === '/api/config' ? {configured:true} :
      {exists:true, model:url === '/api/agent/model' ? 'gpt-5.4' : 'azure-realtime',
        transcriptionModel:'mai-transcribe-2',
        version:url === '/api/agent/model' ? '3' : '2'}};
  }});
  ui.stop();
  ui.element('agent-model-choice').value = 'gpt-5.4';
  ui.element('agent-model-choice').onchange();
  assert.equal(ui.element('voice').hidden, false);
  assert.equal(ui.element('native-voice').hidden, true);
  assert.equal(ui.element('agent-model').textContent, 'azure-realtime');
  assert.equal(ui.element('save-model').disabled, false);
  await ui.element('save-model').onclick();
  assert.deepEqual(writes, [{model:'gpt-5.4', version:'2', transcriptionModel:'mai-transcribe-2'}]);
  assert.equal(ui.element('agent-model').textContent, 'gpt-5.4');
  assert.equal(ui.element('agent-version').textContent, '3');
});

test('A transcription-only selection publishes a new version and reports the actual saved choice', async () => {
  const writes = [];
  const ui = await setup('websocket', {fetch:async (url, options)=>{
    if (url === '/api/agent/model') writes.push(JSON.parse(options.body));
    return {ok:true, json:async()=>url === '/api/config' ? {configured:true} :
      {exists:true, model:'gpt-realtime-2.1', version:url === '/api/agent/model' ? '2' : '1',
        transcriptionModel:url === '/api/agent/model' ? 'mai-transcribe-2' : 'whisper-1'}};
  }});
  ui.stop();
  assert.equal(ui.element('agent-transcription').textContent, 'whisper-1');
  assert.equal(ui.element('agent-transcription-choice').value, 'whisper-1');
  assert.equal(ui.element('save-model').disabled, true);
  ui.element('agent-transcription-choice').value = 'mai-transcribe-2';
  ui.element('agent-transcription-choice').onchange();
  assert.equal(ui.element('agent-transcription').textContent, 'whisper-1');
  assert.equal(ui.element('save-model').disabled, false);
  await ui.element('save-model').onclick();
  assert.deepEqual(writes, [{model:'gpt-realtime-2.1', version:'1', transcriptionModel:'mai-transcribe-2'}]);
  assert.equal(ui.element('agent-transcription').textContent, 'mai-transcribe-2');
  assert.equal(ui.element('agent-version').textContent, '2');
  assert.equal(ui.element('save-model').disabled, true);
});

test('Transcription defaults are planned before creation; active calls and save failures do not overwrite saved labels', async () => {
  const planned = await setup('websocket', {fetch:async url=>({ok:true, json:async()=>url === '/api/config' ?
    {configured:true} : {exists:false, model:'gpt-realtime-2.1', transcriptionModel:'mai-transcribe-2'}})});
  assert.equal(planned.element('agent-transcription').textContent, 'mai-transcribe-2 (after setup)');
  assert.equal(planned.element('agent-transcription-choice').value, 'mai-transcribe-2');
  const ui = await setup('websocket', {fetch:async url=>({ok:url !== '/api/agent/model',
    json:async()=>url === '/api/config' ? {configured:true} : url === '/api/agent/model' ?
      {error:'Transcription model unavailable'} :
      {exists:true, model:'gpt-5.4', version:'1', transcriptionModel:'mai-transcribe-2'}})});
  ui.markReady(ui.session);
  ui.element('agent-transcription-choice').value = 'azure-llm-speech';
  ui.element('agent-transcription-choice').onchange();
  assert.equal(ui.element('agent-transcription-choice').disabled, true);
  assert.equal(ui.element('save-model').disabled, true);
  ui.stop();
  await ui.element('save-model').onclick();
  assert.match(ui.element('error').textContent, /Transcription model unavailable/);
  assert.equal(ui.element('agent-transcription').textContent, 'mai-transcribe-2');
  assert.equal(ui.element('agent-version').textContent, '1');
});

test('Conversation download waits for End and posts only its session capability', async () => {
  const writes = [], blobs = [];
  const ui = await setup('websocket', {
    URL:{createObjectURL:blob=>{blobs.push(blob); return 'blob:recording';}, revokeObjectURL(){}},
    fetch:async (url, options)=>{
      if (url === '/api/conversation/download') {
        writes.push(JSON.parse(options.body));
        return {ok:true, headers:{get:()=>'application/zip'}, blob:async()=>new Blob(['archive'])};
      }
      return {ok:true, json:async()=>({configured:true, exists:true})};
    },
  });
  await ui.handleEvent(ui.session, {type:'bridge.conversation', conversationId:'conv_test', downloadToken:'signed-token'});
  assert.equal(ui.element('download-conversation').disabled, true);
  await ui.element('download-conversation').onclick();
  assert.equal(writes.length, 0);
  ui.stop();
  assert.equal(ui.element('download-conversation').disabled, false);
  await ui.element('download-conversation').onclick();
  assert.deepEqual(writes, [{downloadToken:'signed-token'}]);
  assert.equal(blobs.length, 1);
  const link = ui.created.find(element=>element.tag === 'a');
  assert.equal(link.download, 'conversation-conv_test.zip');
  assert.equal(link.clicked, true);
  assert.match(ui.element('download-status').textContent, /conversation.json and audio.wav/);
});

test('Conversation downloads use the captured call selection instead of the current selection', async () => {
  const requests = [];
  const ui = await setup('websocket', {
    URL:{createObjectURL:()=>'blob:recording', revokeObjectURL(){}},
    fetch:async (url, options)=>{
      if (options?.method === 'POST') {
        requests.push(url);
        return {ok:true, headers:{get:()=>'application/zip'}, blob:async()=>new Blob(['archive'])};
      }
      return {ok:true, json:async()=>({configured:true, exists:true})};
    },
  });
  ui.session.agentSelection = 'live';
  await ui.handleEvent(ui.session, {type:'bridge.conversation', conversationId:'conv_live', downloadToken:'signed-token'});
  ui.stop();
  ui.applyAgentDetails({agentSelection:'base'});
  await ui.element('download-conversation').onclick();
  assert.deepEqual(requests, ['/api/conversation/download?agent_selection=live']);
});

test('Conversation download finishes with its original details when Agents switch mid-request', async () => {
  const requests = [];
  let finishDownload;
  const ui = await setup('websocket', {
    URL:{createObjectURL:()=>'blob:recording', revokeObjectURL(){}},
    fetch:async (url, options)=>{
      if (url.startsWith('/api/conversation/download')) {
        requests.push([url, JSON.parse(options.body)]);
        return new Promise(resolve=>{finishDownload=resolve;});
      }
      const switching = url === '/api/agent/switch';
      return {ok:true, json:async()=>({configured:true, exists:true, version:'1',
        agent:switching ? 'demo-agent-live' : 'demo-agent',
        model:switching ? 'gpt-live-1' : 'gpt-5.4', agentSelection:switching ? 'live' : 'base'})};
    },
  });
  await ui.handleEvent(ui.session, {type:'bridge.conversation', conversationId:'conv_original', downloadToken:'signed-token'});
  ui.stop();
  const pendingDownload = ui.element('download-conversation').onclick();
  ui.element('agent-model-choice').value = 'gpt-live-1';
  await ui.element('save-model').onclick();
  assert.equal(ui.element('agent').textContent, 'demo-agent-live');
  assert.equal(ui.element('download-status').textContent, '');
  finishDownload({ok:true, headers:{get:()=>'application/zip'}, blob:async()=>new Blob(['archive'])});
  await pendingDownload;
  assert.deepEqual(requests, [['/api/conversation/download', {downloadToken:'signed-token'}]]);
  const link = ui.created.find(element=>element.tag === 'a');
  assert.equal(link.download, 'conversation-conv_original.zip');
  assert.equal(link.clicked, true);
  assert.match(ui.element('download-status').textContent, /Download ready/);
  assert.equal(ui.element('download-conversation').disabled, true);
});

test('Scenery controls remain outside the panels they hide', async () => {
  const html = await readFile(new URL('./public/index.html', import.meta.url), 'utf8');
  for (const id of ['weather-motion', 'scenery-toggle', 'scene-city']) {
    assert.equal(html.split(`id="${id}"`).length, 2);
    assert.ok(html.indexOf(`id="${id}"`) < html.indexOf('<header id="app-header"'));
  }
  assert.match(html, /aria-controls="app-header app-content app-footer"/);
  assert.match(html, /Active calls continue/);
  assert.match(html, /aria-describedby="scene-landmark scenery-weather"/);
  assert.match(html, /class="scenery-meta"/);
});

test('Session controls and saved summary precede collapsed Agent and audio settings', async () => {
  const html = await readFile(new URL('./public/index.html', import.meta.url), 'utf8');
  const settings = html.indexOf('<details id="agent-audio-settings"');
  assert.ok(settings > 0);
  assert.match(html, /<details id="agent-audio-settings" class="settings-panel">/);
  for (const id of ['agent-preparation', 'agent-model', 'agent-version', 'agent-transcription', 'start', 'mute', 'stop', 'error']) {
    assert.ok(html.indexOf(`id="${id}"`) < settings, `${id} stays visible outside settings`);
    assert.equal(html.split(`id="${id}"`).length, 2, `${id} is unique`);
  }
  for (const id of ['agent-model-choice', 'agent-transcription-choice', 'transport', 'voice', 'personalization', 'advanced-agent']) {
    assert.ok(html.indexOf(`id="${id}"`) > settings, `${id} is below the primary controls`);
  }
  assert.match(html, /<h2>Audio processing<\/h2>/);
  assert.match(html, /existing Agent may differ/);
});

test('Recording notice is visible before the conversation controls', async () => {
  const html = await readFile(new URL('./public/index.html', import.meta.url), 'utf8');
  assert.match(html, /Audio is saved by default/);
  assert.ok(html.indexOf('id="recording-notice"') < html.indexOf('id="start"'));
  assert.match(html, /id="agent-version"/);
  assert.match(html, /<option value="gpt-5.4">gpt-5.4<\/option>/);
  assert.doesNotMatch(html, /<option value="gpt-4.1">/);
  assert.match(html, /value="en-IN-Diya:DragonHDLatestNeural"/);
  assert.match(html, /value="zh-CN-Xiaoqi:DragonHDFlashLatestNeural"/);
  assert.match(html, /value="zh-CN-Xiaoke:DragonHDFlashLatestNeural"/);
});

test('Agent recreation requires the exact name and cancellation never calls the server', async () => {
  for (const confirmation of [null, '', 'wrong', 'demo-agent ']) {
    const requests = [];
    const ui = await setup('websocket', {
      window:{addEventListener(){}, prompt:()=>confirmation},
      fetch:async url => {
        requests.push(url);
        return {ok:true, json:async()=>({configured:true, agent:'demo-agent', agentRecreateAllowed:true, exists:true})};
      },
    });
    ui.stop();
    if (confirmation === null) await ui.prepareAgent(true);
    else await assert.rejects(ui.prepareAgent(true), /did not match/);
    assert.deepEqual(requests, ['/api/config', '/api/agent']);
    assert.equal(ui.element('recreate-agent').disabled, false);
  }
});

test('Agent recreation posts only the configured name and reports saved replacement', async () => {
  const requests = [];
  const ui = await setup('websocket', {
    window:{addEventListener(){}, prompt:()=> 'demo-agent'},
    fetch:async (url, options) => {
      requests.push([url, options]);
      return {ok:true, json:async()=>url === '/api/config' ?
        {configured:true, agent:'demo-agent', agentRecreateAllowed:true} :
        url === '/api/agent' ? {exists:true} :
        {created:true, recreated:true, personalizationRequired:true}};
    },
  });
  ui.stop();
  await ui.element('recreate-agent').onclick();
  assert.equal(requests[2][0], '/api/agent/recreate');
  assert.equal(requests[2][1].method, 'POST');
  assert.deepEqual(JSON.parse(requests[2][1].body), {confirm_agent:'demo-agent'});
  assert.equal(ui.element('agent-preparation').textContent, 'Your Agent has been reset and saved.');
  assert.equal(ui.element('start').disabled, false);
  assert.equal(ui.element('recreate-agent').disabled, false);
});

test('Agent recreation is blocked during a call and when disabled by the server', async () => {
  const ui = await setup('websocket', {window:{addEventListener(){}, prompt(){throw Error('Must not prompt');}}});
  await assert.rejects(ui.prepareAgent(true), /End the conversation/);
  ui.stop();
  assert.equal(ui.element('recreate-agent').disabled, true);
  await assert.rejects(ui.prepareAgent(true), /disabled by the server/);
});

test('Reset failures surface partial state, restore controls, and recover through non-destructive preparation', async () => {
  const requests = [];
  const ui = await setup('websocket', {
    window:{isSecureContext:true, addEventListener(){}, prompt:()=> 'demo-agent'},
    fetch:async url => {
      requests.push(url);
      return {ok:url !== '/api/agent/recreate', json:async()=>url === '/api/config' ?
        {configured:true, agent:'demo-agent', agentRecreateAllowed:true} :
        url === '/api/agent' ? {exists:true} :
        url === '/api/agent/recreate' ? {error:'Previous Agent removed; use Retry setup & start to recover.'} : {created:true}};
    },
    navigator:{mediaDevices:{getUserMedia:async()=>({getTracks:()=>[]})}},
    location:{protocol:'http:',host:'localhost:8080'},
    PcmAudio:class {resumePromise=Promise.resolve(); prepare(){} dispose(){}},
    WebSocket:class {static OPEN=1; constructor(){this.readyState=1;} close(){}},
    setTimeout:()=>1,
  });
  ui.stop();
  await ui.element('recreate-agent').onclick();
  assert.match(ui.element('error').textContent, /removed.*Retry setup & start/);
  assert.equal(ui.element('error').hidden, false);
  assert.equal(ui.element('start').disabled, false);
  assert.equal(ui.element('recreate-agent').disabled, true);
  await ui.element('start').onclick();
  assert.equal(requests.at(-1), '/api/agent/prepare');
  assert.equal(ui.element('error').hidden, true);
  assert.equal(requests.filter(url=>url === '/api/agent/recreate').length, 1);
  assert.equal(ui.element('recreate-agent').disabled, true);
  ui.stop();
});

test('Agent setup has one primary action with reset inside collapsed Advanced', async () => {
  const page = await readFile(new URL('./public/index.html', import.meta.url), 'utf8');
  assert.ok(!page.includes('id="prepare-agent"'));
  assert.equal(page.match(/id="start"/g).length, 1);
  const advanced = page.match(/<details id="advanced-agent"([^>]*)>([\s\S]*?)<\/details>/);
  assert.ok(advanced);
  assert.ok(!/\bopen\b/.test(advanced[1]));
  assert.match(advanced[2], /<summary>Advanced<\/summary>/);
  assert.match(advanced[2], /id="recreate-agent"[^>]*>Reset Agent<\/button>/);
  const enabled = await setup('websocket', {
    fetch:async()=>({ok:true,json:async()=>({configured:true,agentRecreateAllowed:true,exists:true})}),
  });
  assert.equal(enabled.element('advanced-agent').hidden, false);
  const disabled = await setup('websocket');
  assert.equal(disabled.element('advanced-agent').hidden, true);
});

test('Agent reset disables all lifecycle controls until it completes', async () => {
  let finish;
  const ui = await setup('websocket', {
    window:{addEventListener(){}, prompt:()=> 'demo-agent'},
    fetch:async url => url === '/api/config' ?
      {ok:true, json:async()=>({configured:true, agent:'demo-agent', agentRecreateAllowed:true})} :
      url === '/api/agent' ? {ok:true, json:async()=>({exists:true})} :
      new Promise(resolve => {finish = () => resolve({ok:true, json:async()=>({created:true, recreated:true})});}),
  });
  ui.stop();
  const resetting = ui.prepareAgent(true);
  for (const id of ['start', 'recreate-agent']) assert.equal(ui.element(id).disabled, true);
  await assert.rejects(ui.prepareAgent(true), /already running/);
  finish();
  await resetting;
  for (const id of ['start', 'recreate-agent']) assert.equal(ui.element(id).disabled, false);
});

test('First visit needs no existing Agent and never creates on page load', async () => {
  const requests = [];
  const ui = await setup('websocket', {
    fetch:async (url, options) => {
      requests.push([url, options?.method || 'GET']);
      return {ok:true, json:async()=>url === '/api/config' ?
        {configured:true, agent:'webrtc-personalized-hd-demo', agentRecreateAllowed:true} : {exists:false}};
    },
  });
  assert.deepEqual(requests, [['/api/config','GET'], ['/api/agent','GET']]);
  assert.equal(ui.element('agent').textContent, 'webrtc-personalized-hd-demo');
  assert.equal(ui.element('start').textContent, 'Create Agent & start');
  assert.equal(ui.element('start').disabled, false);
  assert.equal(ui.element('recreate-agent').disabled, true);
  assert.match(ui.element('agent-preparation').textContent, /No Agent yet.*save one for next time/);
  assert.equal(ui.element('weather-tool-hint').hidden, false);
});

test('Saved Agent is rediscovered after a new page session without publishing again', async () => {
  let saved = false, publications = 0;
  const fetch = async url => ({ok:true, json:async()=> {
    if (url === '/api/config') return {configured:true, agent:'webrtc-personalized-hd-demo'};
    if (url === '/api/agent') return {exists:saved, personalizationRequired:saved, weatherEnabled:saved};
    if (url === '/api/agent/prepare') {
      const created = !saved;
      if (created) publications++;
      saved = true;
      return {created, personalizationRequired:true, weatherEnabled:true};
    }
    throw Error(`Unexpected request: ${url}`);
  }});
  const first = await setup('websocket', {fetch});
  first.stop();
  await first.prepareAgent();
  assert.equal(first.element('agent-preparation').textContent, 'Your Agent is saved and ready.');
  const next = await setup('websocket', {fetch});
  assert.equal(next.element('start').textContent, 'Start conversation');
  assert.equal(next.element('agent-preparation').textContent, 'Your Agent is saved and ready.');
  assert.equal(next.element('personalize').checked, true);
  assert.equal(next.element('weather-tool-hint').hidden, false);
  next.stop();
  await next.prepareAgent();
  assert.equal(publications, 1);
});

test('Saved custom Agents without the weather Toolbox are not advertised as weather-enabled', async () => {
  const ui = await setup('websocket');
  assert.equal(ui.element('weather-tool-hint').hidden, true);
});

test('A failed Agent lookup is an explicit error, not a missing Agent or an automatic creation', async () => {
  const requests = [];
  const ui = await setup('websocket', {
    fetch:async url => {
      requests.push(url);
      return {ok:url === '/api/config', json:async()=>url === '/api/config' ?
        {configured:true, agentRecreateAllowed:true} : {error:'Agent lookup denied (HTTP 403)'}};
    },
  });
  assert.deepEqual(requests, ['/api/config', '/api/agent']);
  assert.equal(ui.element('start').textContent, 'Retry setup & start');
  assert.match(ui.element('error').textContent, /lookup denied/);
  assert.equal(ui.element('recreate-agent').disabled, true);
  assert.ok(!ui.element('agent-preparation').textContent.includes('No Agent yet'));
});

for (const fails of [false, true]) {
  test(`First conversation ${fails ? 'does not start after failed setup' : 'waits for saved Agent before microphone and signaling'}`, async () => {
    const actions = [];
    let finish;
    class Socket {
      constructor() { actions.push('signaling'); this.readyState = 1; }
      close() {}
    }
    class Audio {
      resumePromise = Promise.resolve();
      prepare() {}
      dispose() {}
    }
    const ui = await setup('websocket', {
      fetch:async url => {
        if (url === '/api/config') return {ok:true,json:async()=>({configured:true, defaultTransport:'websocket'})};
        if (url === '/api/agent') return {ok:true,json:async()=>({exists:false})};
        actions.push('prepare');
        return new Promise(resolve => { finish = () => {
          actions.push(fails ? 'setup failed' : 'saved');
          resolve({ok:!fails,json:async()=>fails ? {error:'Creation denied'} : {created:true, personalizationRequired:true}});
        }; });
      },
      window:{isSecureContext:true, addEventListener(){}},
      navigator:{mediaDevices:{getUserMedia:async()=> {actions.push('microphone'); return {getTracks:()=>[]};}}},
      location:{protocol:'http:',host:'localhost:8080'}, WebSocket:Socket, PcmAudio:Audio,
      setTimeout:()=>1,
    });
    ui.stop();
    const starting = ui.start();
    assert.deepEqual(actions, ['prepare']);
    assert.equal(ui.element('start').disabled, true);
    finish();
    await starting;
    assert.deepEqual(actions, fails ? ['prepare','setup failed'] : ['prepare','saved','microphone','signaling']);
    if (fails) assert.equal(ui.element('error').textContent, 'Creation denied');
    ui.stop();
  });
}

for (const transport of ['websocket', 'webrtc']) {
  test(`${transport}: personalization precedes signaling and contains no URL context`, async () => {
    let socket;
    class Socket {
      static OPEN = 1;
      constructor(url) { this.url = url; this.sent = []; this.readyState = 1; socket = this; }
      send(raw) { this.sent.push(JSON.parse(raw)); }
      close() {}
    }
    const ui = await setup(transport, {
      WebSocket: Socket, location: { protocol: 'http:', host: 'localhost:8080' },
    });
    ui.session.personalization = { type: 'bridge.personalize', display_name: 'Alex' };
    ui.openSocket(ui.session, ws => ws.send(JSON.stringify({type:'offer'})));
    socket.onopen();
    assert.equal(socket.sent[0].type, 'bridge.personalize');
    assert.equal(socket.sent[1].type, 'offer');
    assert.match(socket.url, /personalize=true/);
    assert.ok(!socket.url.includes('Alex'));
    ui.stop();
    assert.equal(ui.element('personalization').disabled, false);
  });
}

for (const transport of ['websocket', 'webrtc']) {
  for (const stream of ['audio_transcript', 'output_audio_transcript']) {
    test(`${transport}: ${stream} replies stay below delayed user transcription`, async () => {
      const ui = await setup(transport);
      await ui.handleEvent(ui.session, {type:`response.${stream}.done`, item_id:'greeting', transcript:'Hello!'});
      await ui.handleEvent(ui.session, {type:'input_audio_buffer.speech_started', item_id:'question'});
      const question = ui.element('transcript').children[1];
      assert.equal(question.children[0].textContent, 'You');
      assert.equal(question.children[1].textContent, 'Listening...');
      await ui.handleEvent(ui.session, {type:'input_audio_buffer.speech_stopped', item_id:'question'});
      await ui.handleEvent(ui.session, {type:'input_audio_buffer.committed', item_id:'question'});
      await ui.handleEvent(ui.session, {type:'conversation.item.added', item:{
        id:'question', role:'user', content:[{type:'input_audio', transcript:null}],
      }});
      assert.equal(question.children[1].textContent, 'Transcribing...');
      await ui.handleEvent(ui.session, {type:`response.${stream}.delta`, item_id:'reply', delta:'It is sunny.'});
      await ui.handleEvent(ui.session, {type:'conversation.item.input_audio_transcription.delta',
        item_id:'question', delta:'What is '});
      await ui.handleEvent(ui.session, {type:'conversation.item.input_audio_transcription.delta',
        item_id:'question', delta:'the weather?'});
      assert.equal(question.children[1].textContent, 'What is the weather?');
      await ui.handleEvent(ui.session, {type:'conversation.item.input_audio_transcription.completed',
        item_id:'question', transcript:'What is the weather today?'});
      await ui.handleEvent(ui.session, {type:`response.${stream}.done`, item_id:'reply', transcript:'It is sunny.'});
      const rows = ui.element('transcript').children;
      assert.equal(rows.length, 3);
      assert.equal(rows[1], question, 'Final transcription must update the original bubble');
      assert.equal(rows[1].children[1].textContent, 'What is the weather today?');
      assert.equal(rows[2].children[1].textContent, 'It is sunny.');
      assert.equal(rows[1].children[1].className, '');
    });
  }

  for (const earlyEvent of [
    {type:'input_audio_buffer.committed', item_id:'question'},
    {type:'conversation.item.created', item:{id:'question', role:'user', content:[{type:'input_audio'}]}},
    {type:'conversation.item.added', item:{id:'question', role:'user', content:[{type:'input_audio'}]}},
  ]) {
    test(`${transport}: ${earlyEvent.type} reserves user position when speech-start ID is absent`, async () => {
      const ui = await setup(transport);
      await ui.handleEvent(ui.session, {type:'input_audio_buffer.speech_started'});
      await ui.handleEvent(ui.session, earlyEvent);
      await ui.handleEvent(ui.session, {type:'response.output_text.done', item_id:'reply', text:'Four.'});
      await ui.handleEvent(ui.session, {type:'conversation.item.input_audio_transcription.completed',
        item_id:'question', transcript:'What is two plus two?'});
      const rows = ui.element('transcript').children;
      assert.equal(rows.length, 2);
      assert.equal(rows[0].children[0].textContent, 'You');
      assert.equal(rows[0].children[1].textContent, 'What is two plus two?');
      assert.equal(rows[1].children[1].textContent, 'Four.');
    });
  }

  test(`${transport}: multiple user transcripts completing out of order keep their turn positions`, async () => {
    const ui = await setup(transport);
    for (const number of [1, 2]) {
      await ui.handleEvent(ui.session, {type:'input_audio_buffer.speech_started', item_id:`question-${number}`});
      await ui.handleEvent(ui.session, {type:'response.output_text.done', item_id:`reply-${number}`, text:`Answer ${number}`});
    }
    for (const number of [2, 1]) {
      await ui.handleEvent(ui.session, {type:'conversation.item.input_audio_transcription.completed',
        item_id:`question-${number}`, transcript:`Question ${number}`});
    }
    assert.deepEqual(ui.element('transcript').children.map(row=>row.children[1].textContent),
      ['Question 1', 'Answer 1', 'Question 2', 'Answer 2']);
  });

  test(`${transport}: failed, empty, and unfinished transcripts resolve in their reserved bubble`, async () => {
    for (const outcome of ['failed', 'empty', 'stopped']) {
      const ui = await setup(transport);
      await ui.handleEvent(ui.session, {type:'input_audio_buffer.speech_started', item_id:'question'});
      await ui.handleEvent(ui.session, {type:'response.output_text.done', item_id:'reply', text:'Answer'});
      if (outcome === 'stopped') ui.stop();
      else await ui.handleEvent(ui.session, {type:`conversation.item.input_audio_transcription.${outcome === 'failed' ? 'failed' : 'completed'}`,
        item_id:'question', transcript:''});
      const rows = ui.element('transcript').children;
      assert.equal(rows.length, 2);
      assert.equal(rows[0].children[1].textContent, outcome === 'failed' ? 'Speech transcription failed.' :
        outcome === 'empty' ? 'No speech recognized.' : 'Speech transcription unavailable.');
      assert.equal(rows[0].children[1].className, '');
      assert.equal(rows[1].children[1].textContent, 'Answer');
      if (outcome === 'failed') assert.match(ui.element('error').textContent, /Speech transcription failed/);
    }
  });

  test(`${transport}: sends text and a response request over the bridge socket`, async () => {
    const ui = await setup(transport);
    ui.element('text-input').value = '  Hello <world>  ';
    ui.updateTextInput();
    assert.equal(ui.element('send-text').disabled, true);
    await ui.handleEvent(ui.session, {type:'bridge.ready'});
    assert.equal(ui.element('text-input').disabled, false);
    assert.equal(ui.element('send-text').disabled, false);
    let prevented = false;
    ui.element('text-form').onsubmit({ preventDefault() { prevented = true; } });
    assert.equal(prevented, true);
    const id = ui.sent[0].item.id;
    assert.equal(id.length, 32);
    assert.match(id, /^[a-f0-9]{32}$/);
    assert.deepEqual(ui.sent, [
      { type: 'conversation.item.create', item: {
        id, type: 'message', role: 'user',
        content: [{ type: 'input_text', text: 'Hello <world>' }],
      } },
      { type: 'response.create', response: { output_modalities: ['text'] } },
    ]);
    assert.equal(ui.element('text-input').value, '');
    assert.equal(ui.element('transcript').children[0].children[1].textContent, 'Hello <world>');
    await ui.handleEvent(ui.session, {type:'conversation.item.created', item:ui.sent[0].item});
    assert.equal(ui.element('transcript').children.length, 1, 'Server echo must not duplicate a typed message');
    ui.element('text-input').value = 'Next';
    ui.element('text-input').oninput();
    assert.equal(ui.element('send-text').disabled, true);
    ui.element('text-form').onsubmit({ preventDefault() {} });
    assert.equal(ui.sent.length, 2);
    await ui.handleEvent(ui.session, { type: 'response.created', response: { id: 'reply' } });
    await ui.handleEvent(ui.session, { type: 'response.output_text.delta', item_id: 'answer', delta: 'Hello back' });
    await ui.handleEvent(ui.session, { type: 'response.output_text.done', item_id: 'answer', text: 'Hello back' });
    assert.equal(ui.element('transcript').children[1].children[1].textContent, 'Hello back');
    await ui.handleEvent(ui.session, { type: 'response.done', response: { status: 'completed' } });
    assert.equal(ui.element('send-text').disabled, false);
    ui.stop();
    assert.equal(ui.element('text-input').disabled, true);
    assert.equal(ui.element('send-text').disabled, true);
  });

  test(`${transport}: rejects blank, oversized, speaking and disconnected submissions`, async () => {
    const ui = await setup(transport);
    await ui.handleEvent(ui.session, {type:'bridge.ready'});
    for (const text of [' ', 'x'.repeat(4001)]) {
      ui.element('text-input').value = text;
      ui.element('text-form').onsubmit({ preventDefault() {} });
    }
    assert.equal(ui.sent.length, 0);
    assert.equal(ui.element('error').hidden, false);
    ui.element('text-input').value = 'Hello';
    await ui.handleEvent(ui.session, { type: 'input_audio_buffer.speech_started' });
    assert.equal(ui.element('send-text').disabled, true);
    ui.element('text-form').onsubmit({ preventDefault() {} });
    await ui.handleEvent(ui.session, { type: 'input_audio_buffer.speech_stopped' });
    ui.session.ws.readyState = 3;
    ui.updateTextInput();
    assert.equal(ui.element('text-input').disabled, true);
    ui.element('text-form').onsubmit({ preventDefault() {} });
    assert.equal(ui.sent.length, 0);
  });

  test(`${transport}: failed responses surface errors and allow retry`, async () => {
    const ui = await setup(transport);
    await ui.handleEvent(ui.session, {type:'bridge.ready'});
    ui.element('text-input').value = 'Hello';
    await ui.handleEvent(ui.session, { type: 'response.created', response: { id: 'reply' } });
    assert.equal(ui.element('send-text').disabled, true);
    await ui.handleEvent(ui.session, { type: 'response.done', response: { status: 'failed' } });
    assert.equal(ui.element('error').hidden, false);
    assert.equal(ui.element('send-text').disabled, false);
  });

  test(`${transport}: send failures preserve the draft and disable the composer`, async () => {
    const ui = await setup(transport);
    await ui.handleEvent(ui.session, {type:'bridge.ready'});
    ui.element('text-input').value = 'Keep my draft';
    ui.session.ws.send = () => { throw new Error('Socket send failed'); };
    ui.element('text-form').onsubmit({ preventDefault() {} });
    assert.equal(ui.element('text-input').value, 'Keep my draft');
    assert.equal(ui.element('text-input').disabled, true);
    assert.equal(ui.element('error').textContent, 'Socket send failed');
    assert.equal(ui.element('transcript').children.length, 0);
  });
}

async function setupSignaling(overrides = {}) {
  class Socket {
    static OPEN = 1;
    constructor() { this.readyState = 0; this.sent = []; }
    send(frame) { this.sent.push(JSON.parse(frame)); }
    close() { this.readyState = 3; }
    open() { this.readyState = 1; this.onopen(); }
  }
  const ui = await setup('webrtc', {
    WebSocket: Socket, location: { protocol: 'https:', host: 'example.test' },
    setTimeout, clearTimeout, ...overrides,
  });
  const peer = new EventTarget();
  peer.iceGatheringState = 'gathering';
  peer.createOffer = async () => ({type:'offer', sdp:'v=0\r\n'});
  peer.setLocalDescription = async offer => { peer.localDescription = offer; };
  peer.complete = () => {
    peer.localDescription.sdp += 'a=candidate:complete\r\n';
    peer.iceGatheringState = 'complete';
    peer.dispatchEvent(new Event('icegatheringstatechange'));
  };
  peer.close = () => {};
  ui.session.peer = peer;
  return {...ui, peer};
}

test('WebRTC opens signaling before ICE completes and sends the complete offer once', async () => {
  const ui = await setupSignaling();
  const connecting = ui.connectWebRtc(ui.session);
  const ws = ui.session.ws;
  assert.ok(ws, 'Socket must open without waiting for ICE');
  ws.open();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(ws.sent.length, 0, 'An incomplete SDP must not be sent');
  ui.peer.complete();
  await connecting;
  assert.deepEqual(ws.sent, [{type:'rtc.call.sdp.create', sdp_offer:ui.peer.localDescription.sdp}]);
  ws.onopen();
  assert.equal(ws.sent.length, 1);
  ui.stop();
});

test('WebRTC waits for socket opening when ICE completes first', async () => {
  const ui = await setupSignaling();
  const connecting = ui.connectWebRtc(ui.session);
  await new Promise(resolve => setImmediate(resolve));
  ui.peer.complete();
  await connecting;
  assert.equal(ui.session.ws.sent.length, 0);
  ui.session.ws.open();
  assert.equal(ui.session.ws.sent.length, 1);
  ui.stop();
});

test('WebRTC cancellation during ICE closes signaling without sending an offer', async () => {
  const ui = await setupSignaling();
  const connecting = ui.connectWebRtc(ui.session);
  ui.session.ws.open();
  await new Promise(resolve => setImmediate(resolve));
  ui.stop();
  await assert.rejects(connecting, /Session cancelled/);
  assert.equal(ui.session.ws.readyState, 3);
  assert.equal(ui.session.ws.sent.length, 0);
});

test('WebRTC reports an ICE timeout and does not send an incomplete offer', async () => {
  let timeout;
  const ui = await setupSignaling({setTimeout: callback => { timeout = callback; return 1; }, clearTimeout() {}});
  const connecting = ui.connectWebRtc(ui.session);
  ui.session.ws.open();
  await new Promise(resolve => setImmediate(resolve));
  timeout();
  await assert.rejects(connecting, /ICE gathering timed out/);
  assert.equal(ui.session.ws.sent.length, 0);
  ui.stop();
});

for (const transport of ['websocket', 'webrtc']) {
  test(`${transport}: retained replies preserve reserved question order and cleared turns stay hidden`, async () => {
    const ui = await setup(transport);
    await ui.handleEvent(ui.session, {type:'input_audio_buffer.speech_started', item_id:'question'});
    await ui.handleEvent(ui.session, {type:'response.audio_transcript.delta',
      item_id:'answer', delta:'Heard UNHEARD'});
    await ui.handleEvent(ui.session, {type:'conversation.item.truncated', item_id:'answer',
      content_index:0, audio_end_ms:100,
      item:{id:'answer', type:'message', role:'assistant', content:[{type:'audio', transcript:'Heard'}]}});
    await ui.handleEvent(ui.session, {type:'conversation.item.input_audio_transcription.completed',
      item_id:'question', transcript:'Question'});
    assert.deepEqual(ui.element('transcript').children.map(row => row.children[1].textContent), ['Question', 'Heard']);
    await ui.handleEvent(ui.session, {type:'input_audio_buffer.speech_started', item_id:'pending'});
    ui.element('clear').onclick();
    await ui.handleEvent(ui.session, {type:'conversation.item.input_audio_transcription.delta',
      item_id:'question', delta:'Late text'});
    await ui.handleEvent(ui.session, {type:'conversation.item.input_audio_transcription.completed',
      item_id:'pending', transcript:'Late question'});
    await ui.handleEvent(ui.session, {type:'response.audio_transcript.done',
      item_id:'answer', transcript:'Heard UNHEARD'});
    ui.stop();
    assert.equal(ui.element('transcript').children.length, 0);
  });

  test(`${transport}: input waits for bridge configuration and media readiness`, async () => {
    const ui = await setup(transport);
    const track = {enabled:false};
    ui.session.mic = {getAudioTracks:() => [track], getTracks:() => []};
    if (ui.session.peer) ui.session.peer.connectionState = 'connecting';
    ui.markReady(ui.session);
    await ui.handleEvent(ui.session, {type:'session.updated', session:{}});
    assert.equal(ui.session.ready, false);
    assert.equal(track.enabled, false);
    await ui.handleEvent(ui.session, {type:'bridge.ready'});
    assert.equal(ui.session.ready, transport === 'websocket');
    if (ui.session.peer) {
      ui.session.peer.connectionState = 'connected';
      ui.markReady(ui.session);
    }
    assert.equal(track.enabled, true);
    assert.equal(ui.element('text-input').disabled, false);
    ui.stop();
  });

  test(`${transport}: rendered retained history replaces drafts, clears, and isolates reconnects`, async () => {
    const ui = await setup(transport);
    await ui.handleEvent(ui.session, {type:'response.audio_transcript.delta', item_id:'answer', delta:'Heard UNHEARD'});
    const box = ui.element('transcript').children[0];
    await ui.handleEvent(ui.session, {type:'conversation.item.truncated', item_id:'answer', content_index:0, audio_end_ms:100,
      item:{id:'answer', type:'message', role:'assistant', content:[{type:'audio', transcript:'Heard'}]}});
    await ui.handleEvent(ui.session, {type:'response.output_audio_transcript.done', item_id:'answer', transcript:'Heard UNHEARD'});
    assert.equal(ui.element('transcript').children.length, 1);
    assert.equal(box.children[1].textContent, 'Heard');
    assert.match(box.children[0].textContent, /retained after interruption/);
    ui.element('clear').onclick();
    await ui.handleEvent(ui.session, {type:'response.text.done', item_id:'answer', text:'Full'});
    assert.equal(ui.element('transcript').children.length, 0);
    ui.stop();
    await ui.handleEvent(ui.session, {type:'response.text.done', item_id:'answer', text:'Old session'});
    assert.equal(ui.element('transcript').children.length, 0);
    ui.reconnect();
    await ui.handleEvent(ui.session, {type:'response.text.done', item_id:'answer', text:'New session'});
    assert.equal(ui.element('transcript').children[0].children[1].textContent, 'New session');
    ui.stop();
  });

  test(`${transport}: missing-item retrieval uses the socket and an empty result clears the card`, async () => {
    const ui = await setup(transport);
    await ui.handleEvent(ui.session, {type:'response.text.done', item_id:'answer', text:'Full'});
    await ui.handleEvent(ui.session, {type:'conversation.item.truncated', item_id:'answer', content_index:0, audio_end_ms:0});
    assert.equal(ui.sent[0].type, 'conversation.item.retrieve');
    assert.equal(ui.sent[0].item_id, 'answer');
    assert.equal(ui.element('transcript').children[0].children[1].textContent, '');
    await ui.handleEvent(ui.session, {type:'conversation.item.retrieved',
      item:{id:'answer', type:'message', role:'assistant', content:[]}});
    assert.match(ui.element('transcript').children[0].children[0].textContent, /no text retained/);
    ui.stop();
  });
}

import test from 'node:test';
import assert from 'node:assert/strict';
import { setupLiveVoice } from './controller.js';

function fixture(denied=false) {
  const elements={start:{},end:{},status:{}};
  const reports=[],calls=[];
  let render;
  const session={endSession:async () => { calls.push('end'); }};
  let number=0;
  setupLiveVoice({
    document:{getElementById:id=>elements[id]},window:{addEventListener:()=>{}},
    crypto:{randomUUID:()=>`request-${++number}`},
    navigator:{mediaDevices:{getUserMedia:async()=>{
      if (denied) throw new Error('private error');
      return {getTracks:()=>[{stop:()=>{}}]};
    }}},
    Conversation:{startSession:async options=>{calls.push(options);return session;}},
    Streamlit:{RENDER_EVENT:'render',events:{addEventListener:(type,fn)=>{render=fn;}},
      setComponentValue:value=>reports.push(value),setFrameHeight:()=>{},setComponentReady:()=>{}}
  });
  return {elements,reports,calls,render:args=>render({detail:{args}})};
}

test('explicit Start, one session across rerenders, End, and a second Start',async()=>{
  const f=fixture();
  await f.render({});
  assert.equal(f.calls.length,0);
  await f.elements.start.onclick();
  assert.equal(f.reports[0].action,'start');
  const args={request_id:'request-1',signed_url:'wss://api.elevenlabs.io/private'};
  await f.render(args); await f.render(args);
  assert.equal(f.calls.length,1);
  assert.equal(f.calls[0].connectionType,'websocket');
  assert.equal(f.calls[0].onMessage,undefined);
  await f.elements.end.onclick();
  assert.equal(f.calls[1],'end');
  await f.elements.start.onclick();
  await f.render({request_id:'request-2',signed_url:args.signed_url});
  assert.equal(f.calls.length,3);
});

test('mic denial does not request a signed URL',async()=>{
  const f=fixture(true);
  await f.elements.start.onclick();
  assert.equal(f.reports.length,0);
  assert.match(f.elements.status.textContent,/permission denied/);
  assert.equal(f.elements.start.disabled,false);
});

test('End while minting ignores a late URL and server errors permit restart',async()=>{
  const f=fixture();
  await f.elements.start.onclick(); await f.elements.end.onclick();
  await f.render({request_id:'request-1',signed_url:'wss://api.elevenlabs.io/late'});
  assert.equal(f.calls.length,0);
  await f.elements.start.onclick();
  await f.render({request_id:'request-2',error:true});
  assert.equal(f.elements.start.disabled,false);
});

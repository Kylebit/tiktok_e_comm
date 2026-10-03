"use strict";
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const {createResponseBodyCapture}=require('./response_body_capture.cjs');
const mode=process.argv[2];
const response=(url,body)=>({url:()=>url,body});

(async()=>{
  if(mode==='navigation'){
    const seen=[];let complete;
    const queue=createResponseBodyCapture((reply,raw)=>seen.push({url:reply.url(),sha:crypto.createHash('sha256').update(raw).digest('hex')}));
    const bytes=Buffer.from([0,1,2,255]);
    queue.capture(response('owned/asset.png',()=>new Promise(resolve=>{complete=()=>resolve(bytes);})));
    queue.capture(response('owned/asset.png',()=>Promise.resolve(bytes)));
    let navigation=false;
    const navigationBarrier=(async()=>{await queue.drain();navigation=true;})();
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(navigation,false,'navigation must not invalidate a pending body');
    assert.equal(seen.length,1,'duplicate responses remain independent');
    complete();await navigationBarrier;
    assert.equal(navigation,true);assert.equal(seen.length,2);
    assert.equal(queue.attemptedCount,2);assert.equal(queue.pendingCount,0);
    assert.deepEqual(seen.map(x=>x.sha),Array(2).fill(crypto.createHash('sha256').update(bytes).digest('hex')));
  }else if(mode==='rejection'){
    const unhandled=[];const listener=error=>unhandled.push(error);
    process.on('unhandledRejection',listener);
    try{
      const failure=new Error('exact response body read failed');
      const queue=createResponseBodyCapture(()=>assert.fail('a failed body cannot be accepted'));
      queue.capture(response('owned/failed.png',()=>Promise.reject(failure)));
      await new Promise(resolve=>setImmediate(resolve));
      assert.deepEqual(unhandled,[],'event handler must handle rejection immediately');
      await assert.rejects(queue.drain(),error=>error instanceof AggregateError && error.errors.length===1 && error.errors[0]===failure);
      assert.equal(queue.failures.length,1);assert.equal(queue.failures[0].url,'owned/failed.png');
      await assert.rejects(queue.drain(),AggregateError,'later cleanup cannot forget a failed response');
    }finally{process.off('unhandledRejection',listener);}
  }else if(mode==='late'){
    const seen=[];let first;
    const queue=createResponseBodyCapture((reply,raw)=>{
      seen.push(raw.toString());
      if(raw.toString()==='first')queue.capture(response('owned/late',()=>Promise.resolve(Buffer.from('late'))));
    });
    queue.capture(response('owned/first',()=>new Promise(resolve=>{first=resolve;})));
    const draining=queue.drain();await new Promise(resolve=>setImmediate(resolve));
    first(Buffer.from('first'));await draining;
    assert.deepEqual(seen,['first','late'],'drain must include captures added while waiting');
    assert.equal(queue.attemptedCount,2);assert.equal(queue.pendingCount,0);
    const broken=createResponseBodyCapture(()=>{throw new Error('observer callback failure');});
    broken.capture(response('owned/callback',()=>Promise.resolve(Buffer.from('raw'))));
    await assert.rejects(broken.drain(),error=>error instanceof AggregateError && error.errors[0].message==='observer callback failure');
  }else if(mode==='late-response'){
    let navigation=false;
    const request={url:()=> 'owned/late-response.png'};
    const queue=createResponseBodyCapture(()=>{});
    // Exercise the old response-only helper too: absent lifecycle support must
    // still demonstrate premature navigation rather than an API TypeError.
    queue.requestStarted?.(request);
    const barrier=(async()=>{await queue.drain();navigation=true;})();
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(navigation,false,'navigation must wait for an owned request whose response has not arrived');
    queue.capture({...response(request.url(),()=>Promise.resolve(Buffer.from('late response'))),request:()=>request});
    queue.requestFinished?.(request);
    await barrier;
    assert.equal(navigation,true);assert.equal(queue.attemptedCount,1);assert.equal(queue.pendingCount,0);
  }else throw new Error('unknown contract mode');
  process.stdout.write(JSON.stringify({ok:true,mode})+'\n');
})().catch(error=>{console.error(error);process.exitCode=1;});

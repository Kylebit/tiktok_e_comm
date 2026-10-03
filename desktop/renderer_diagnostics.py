"""Read-only diagnostics through the actual embedded WebView2, not a second browser."""
from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Event
import time


def devtools(app, method, parameters=None):
    """Internal renderer helper; it is never exposed to page JavaScript."""
    from System import Action, Func, Object, String
    from System.Threading.Tasks import Task
    complete = Event()
    result = {}

    def receive(task):
        try:
            result['value'] = json.loads(str(task.Result))
        except BaseException as error:
            result['error'] = str(error)
        finally:
            complete.set()

    app.native.Invoke(Func[Object](lambda: app.native.webview.CoreWebView2.CallDevToolsProtocolMethodAsync(
        method, json.dumps(parameters or {})).ContinueWith(Action[Task[String]](receive),
        app.native.browser.syncContextTaskScheduler)))
    if not complete.wait(15):
        raise RuntimeError('WebView2 diagnostic command timed out')
    if 'error' in result:
        raise RuntimeError(result['error'])
    return result['value']


def record(app, directory: Path, *, close=False, expected_url=None, product_images=False, workspace_readonly=False):
    import webview
    from System import Func, Object
    directory.mkdir(parents=True, exist_ok=True)
    report = {'pid': os.getpid(), 'renderer': webview.guilib.renderer,
              'evidence_layer': 'actual WebView2 application test interface',
              'native_human_UI_acceptance': False, 'status': app.session.last_result}
    try:
        # Native CapturePreview requires a visible composited surface on this
        # renderer; diagnostics show the same APP window before capturing it.
        app.window.show()
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            state = app.window.evaluate_js("({url:location.href,ready:document.readyState,title:document.title})")
            if state and state['ready'] == 'complete' and (not app.session.last_result['healthy'] or state['url'] == (expected_url or app.session.selection.url)):
                break
            time.sleep(.1)
        else:
            raise RuntimeError('selected page did not finish loading')
        if product_images or workspace_readonly:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                identity = app.window.evaluate_js("""(()=>{const offer=new URL(location.href).searchParams.get('offer_id');return {
                    path:location.pathname,offer,selected:document.querySelector('#offerId')?.value,
                    ready:!!document.querySelector('#tab-images') && document.querySelector('#productDetail')?.hidden === false}})()""")
                if identity['path'] != '/product-workspace' or not identity.get('offer'):
                    raise RuntimeError('image diagnostic requires an explicit product workspace deep link')
                if identity['ready'] and identity['selected'] == identity['offer']:
                    break
                time.sleep(.1)
            else:
                raise RuntimeError('selected product is not ready for the read-only image tab')
            if workspace_readonly:
                report['workspace_readonly'] = workspace_journey(app, directory, identity['offer'])
            # This existing consumer only switches pane visibility; it does not
            # save images, approve facts, submit jobs or call a business API.
            devtools(app, 'Runtime.evaluate', {'expression':"document.getElementById('tab-images').click(); document.getElementById('embeddedImageReview').scrollIntoView({block:'start'})",
                                              'userGesture':True,'returnByValue':True})
            report['diagnostic_action'] = 'open-read-only-product-images-tab'
            report['product_identity'] = identity
        image_deadline = time.monotonic() + 5
        previous, stable_since = None, time.monotonic()
        while time.monotonic() < image_deadline:
            images = app.window.evaluate_js('Array.from(document.images).map(i=>({src:i.currentSrc,complete:i.complete,width:i.naturalWidth,height:i.naturalHeight}))')
            if images != previous:
                previous, stable_since = images, time.monotonic()
            if all(item['complete'] for item in images) and time.monotonic() - stable_since >= .5:
                break
            time.sleep(.1)
        report['image_wait_timed_out'] = time.monotonic() >= image_deadline
        report['version'] = app.native.Invoke(Func[Object](lambda: app.native.webview.CoreWebView2.Environment.BrowserVersionString))
        report['document'] = app.window.evaluate_js("""({url:location.href,title:document.title,
            iframe_count:document.querySelectorAll('iframe').length,
            images:Array.from(document.images).map(i=>{const r=i.getBoundingClientRect();return {src:i.currentSrc,complete:i.complete,width:i.naturalWidth,height:i.naturalHeight,
                visible_in_layout:r.width>0 && r.height>0,visible_in_viewport:r.width>0 && r.height>0 && r.bottom>0 && r.right>0 && r.top<innerHeight && r.left<innerWidth}}),
            selected_product_tab:document.querySelector('[data-product-tab][aria-selected="true"]')?.dataset.productTab || null,
            body:document.body.innerText.slice(0,12000),
            exposed_service_methods:window.pywebview ? Object.keys(window.pywebview.api || {}) : []})""")
        if report['document']['url'].endswith('/?view=knowledge'):
            report['tool_cards']=[]
            for key in ('tikhub','duoplus','publication-knowledge'):
                selector='[data-tool="'+key+'"]'
                card=app.window.evaluate_js("(()=>{const card=document.querySelector("+json.dumps(selector)+");if(!card)return null;card.scrollIntoView({block:'center'});const r=card.getBoundingClientRect();return {text:card.innerText,visible:r.width>0&&r.height>0&&r.top<innerHeight&&r.bottom>0}})()")
                if card is None or not card['visible']:raise RuntimeError('knowledge tool card is not visible: '+key)
                report['tool_cards'].append({'id':key,**card})
                (directory/('tool-'+key+'.png')).write_bytes(capture_preview(app))
            app.window.evaluate_js('scrollTo(0,0)')
        captured = capture_preview(app)
        with (directory / 'renderer.png').open('xb') as stream:
            stream.write(captured)
        report['ok'] = True
    except BaseException as error:
        report.update(ok=False, error=str(error))
    finally:
        report['events'] = app.events
        with (directory / 'renderer.json').open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        if close:
            app.window.destroy()
    return report


def workspace_journey(app, directory, offer):
    """Opt-in acceptance through existing read-only controls in the same renderer."""
    steps=[]
    for tab in ('facts','images','targets','release'):
        devtools(app,'Runtime.evaluate',{'expression':f"document.getElementById('tab-{tab}').click();scrollTo(0,0)",'userGesture':True})
        state=app.window.evaluate_js("""(()=>{const active=document.querySelector('[data-product-tab][aria-selected="true"]');
            const panel=document.getElementById(active.getAttribute('aria-controls'));const r=panel.getBoundingClientRect();
            return {tab:active.dataset.productTab,visible:r.width>0&&r.height>0&&getComputedStyle(panel).display!=='none',
            overflow:document.documentElement.scrollWidth>innerWidth+1,offer:document.getElementById('offerId').value}})()""")
        if state['tab']!=tab or not state['visible'] or state['overflow'] or state['offer']!=offer:
            raise RuntimeError('workspace tab/identity/layout mismatch: '+json.dumps(state))
        (directory/('tab-'+tab+'.png')).write_bytes(capture_preview(app));steps.append(state)
    # The diagnostic state directory is new and the deep link supplies one row.
    # Refuse broader queue state before the read-only batch refresh.
    queue=app.window.evaluate_js("Array.from(document.querySelectorAll('#queueGrid tbody tr[data-key]')).map(r=>r.dataset.key)")
    if queue!=[offer]:raise RuntimeError('read-only diagnostic requires only the explicitly selected Offer in its new queue')
    devtools(app,'Runtime.evaluate',{'expression':"document.getElementById('selectFilteredButton').click();document.getElementById('refreshSelectedButton').click()",'userGesture':True})
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        state=app.window.evaluate_js("({busy:document.getElementById('refreshAllButton').disabled,message:document.getElementById('queueMessage').textContent,selection:document.getElementById('queueSelectionCount').textContent})")
        if not state['busy'] and '刷新完成' in state['message']:break
        time.sleep(.1)
    else:raise RuntimeError('read-only selected refresh did not complete')
    if '共更新 1 件' not in state['message']:raise RuntimeError('read-only refresh changed scope')
    return {'tabs':steps,'queue_offer_ids':queue,'batch_refresh':state,'scope':'single explicit synthetic Offer; no save/publish actions'}


def capture_preview(app):
    """Use WebView2's native image stream; no other browser or desktop surface."""
    from Microsoft.Web.WebView2.Core import CoreWebView2CapturePreviewImageFormat
    from System import Action, Func, Object
    from System.IO import MemoryStream
    from System.Threading.Tasks import Task
    complete=Event()
    result={}
    stream=MemoryStream()

    def receive(task):
        try:
            task.GetAwaiter().GetResult()
            result['image']=bytes(stream.ToArray())
        except BaseException as error:
            result['error']=str(error)
        finally:
            stream.Dispose()
            complete.set()

    app.native.Invoke(Func[Object](lambda: app.native.webview.CoreWebView2.CapturePreviewAsync(
        CoreWebView2CapturePreviewImageFormat.Png,stream).ContinueWith(Action[Task](receive),
        app.native.browser.syncContextTaskScheduler)))
    if not complete.wait(15):raise RuntimeError('WebView2 CapturePreviewAsync timed out')
    if 'error' in result:raise RuntimeError(result['error'])
    return result['image']

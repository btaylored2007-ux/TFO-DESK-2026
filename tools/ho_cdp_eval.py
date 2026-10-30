import sys,json,time,subprocess,tempfile,shutil,urllib.request,websocket
URL=sys.argv[1]; WAIT=float(sys.argv[2]) if len(sys.argv)>2 else 20; JS=sys.argv[3] if len(sys.argv)>3 else "document.querySelector('#connection')&&document.querySelector('#connection').innerText"
ud=tempfile.mkdtemp();P=9341
pr=subprocess.Popen(["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome","--headless=new","--disable-gpu","--remote-debugging-port=%d"%P,"--remote-allow-origins=*","--user-data-dir="+ud,"about:blank"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
  for _ in range(50):
    try: tabs=json.load(urllib.request.urlopen("http://127.0.0.1:%d/json"%P,timeout=2));break
    except Exception: time.sleep(.3)
  ws=websocket.create_connection([t for t in tabs if t['type']=='page'][0]['webSocketDebuggerUrl'],timeout=60);n=[0];logs=[]
  def cmd(m,**p):
    n[0]+=1;i=n[0];ws.send(json.dumps({"id":i,"method":m,"params":p}))
    while True:
      r=json.loads(ws.recv())
      if r.get('id')==i: return r.get('result',{})
      if r.get('method') in('Runtime.exceptionThrown','Runtime.consoleAPICalled','Log.entryAdded'): logs.append(json.dumps(r['params'])[:400])
  cmd("Runtime.enable");cmd("Log.enable");cmd("Page.enable");cmd("Page.navigate",url=URL)
  t=time.time()
  ws.settimeout(1)
  while time.time()-t<WAIT:
    try:
      r=json.loads(ws.recv())
      if r.get('method') in('Runtime.exceptionThrown','Runtime.consoleAPICalled','Log.entryAdded'): logs.append(json.dumps(r['params'])[:400])
    except Exception: pass
  ws.settimeout(60)
  print("EVAL:",cmd("Runtime.evaluate",expression=JS,returnByValue=True).get('result',{}).get('value'))
  for l in logs[:15]: print(l)
finally:
  pr.terminate();shutil.rmtree(ud,ignore_errors=True)

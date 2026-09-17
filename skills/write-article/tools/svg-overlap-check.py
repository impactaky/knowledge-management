import re, sys, unicodedata
def wide(c): return unicodedata.east_asian_width(c) in ('W','F','A')
def tw(t,fs): return sum(fs*(1.0 if wide(c) else 0.52) for c in t)
def attr(attrs,n,d=None):
    m=re.search(r'%s="([^"]*)"'%n,attrs); return m.group(1) if m else d

def check(path):
    s=open(path,encoding='utf-8').read()
    svgs=re.findall(r'<svg[^>]*viewBox="([^"]+)"[^>]*>(.*?)</svg>',s,re.S)
    caps=re.findall(r'<span class="fignum">図(\d+)</span>',s)
    tok=re.compile(r'<(/?)(g|text|rect|polygon)\b([^>]*?)(/?)>',re.S)
    total=0
    for idx,(vb,body) in enumerate(svgs):
        vx,vy,vw,vh=map(float,vb.split()); fig=caps[idx] if idx<len(caps) else '?'
        # 継承する属性をスタックで持つ
        st=[{'dx':0.0,'dy':0.0,'anchor':'start','fs':16.0}]
        texts=[];shapes=[];pos=0;order=0
        while True:
            m=tok.search(body,pos)
            if not m: break
            cl,tag,attrs,sc=m.groups(); order+=1
            cur=st[-1]
            if tag=='g':
                if cl:
                    if len(st)>1: st.pop()
                elif not sc:
                    t=re.search(r'transform="translate\(\s*([-\d.]+)[ ,]+([-\d.]+)',attrs)
                    d=(float(t.group(1)),float(t.group(2))) if t else (0.,0.)
                    st.append({'dx':cur['dx']+d[0],'dy':cur['dy']+d[1],
                               'anchor':attr(attrs,'text-anchor',cur['anchor']),
                               'fs':float(attr(attrs,'font-size',cur['fs']))})
                pos=m.end(); continue
            if tag in ('rect','polygon'):
                f=attr(attrs,'fill','none')
                if f not in ('none',None) and float(attr(attrs,'opacity','1'))>0.3:
                    if tag=='rect':
                        x=float(attr(attrs,'x','0'))+cur['dx']; y=float(attr(attrs,'y','0'))+cur['dy']
                        shapes.append((order,x,y,x+float(attr(attrs,'width','0')),y+float(attr(attrs,'height','0')),tag))
                    else:
                        p=attr(attrs,'points','')
                        if p:
                            cs=[float(v) for v in re.split(r'[ ,]+',p.strip())]
                            shapes.append((order,min(cs[0::2])+cur['dx'],min(cs[1::2])+cur['dy'],
                                           max(cs[0::2])+cur['dx'],max(cs[1::2])+cur['dy'],tag))
                pos=m.end(); continue
            if cl: pos=m.end(); continue
            e=body.find('</text>',m.end()); inner=body[m.end():e]; pos=e+7
            if 'transform' in attrs: continue
            x=float(attr(attrs,'x','0'))+cur['dx']; y=float(attr(attrs,'y','0'))+cur['dy']
            fs=float(attr(attrs,'font-size',cur['fs']))
            an=attr(attrs,'text-anchor',cur['anchor'])
            t=re.sub(r'<[^>]+>','',inner).strip()
            if not t: continue
            w=tw(t,fs)
            x0 = x-w/2 if an=='middle' else (x-w if an=='end' else x)
            texts.append((order,x0,y-fs*.82,x0+w,y+fs*.2,t))
        for i,(to,ax0,ay0,ax1,ay1,at) in enumerate(texts):
            if ax1>vw+1 or ax0<vx-1 or ay1>vh+1:
                print(f'図{fig} はみ出し: {at[:44]}'); total+=1
            for (bo,bx0,by0,bx1,by1,bt) in texts[i+1:]:
                if min(ax1,bx1)-max(ax0,bx0)>2 and min(ay1,by1)-max(ay0,by0)>2:
                    print(f'図{fig} 文字同士: "{at[:26]}"／"{bt[:26]}"'); total+=1
            for (so,sx0,sy0,sx1,sy1,k) in shapes:
                if so>to and min(ax1,sx1)-max(ax0,sx0)>3 and min(ay1,sy1)-max(ay0,sy0)>3:
                    print(f'図{fig} 覆われる: "{at[:36]}"'); total+=1
    print(f'--- 全{len(svgs)}図 / 問題 {total} 件 ---')
    for t in ['svg','g','figure','figcaption','text','div','table','tr','td','th','ol','li','p','h2','h3','pre','ul','span','a','tspan']:
        o=len(re.findall(r'<%s[ >]'%t,s)); c=len(re.findall(r'</%s>'%t,s))
        if o!=c: print(f'*** タグ不一致 {t}: {o}/{c}')
    print('タグ検査OK / 図番号:', caps)

check(sys.argv[1])

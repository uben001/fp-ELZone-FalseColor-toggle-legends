"""Emulate modified ARM handlers; no USB or camera access."""
import struct
import verify_fcscale as v
import build_fcscale_autorun as b
class Camera(v.Camera):
    def __init__(self):
        super().__init__()
        self.u.mem_map(0xc3074000,0x2000)
        self.u.mem_write(0xc3075230,struct.pack('<I',0xc3074be8))
        self.auto_native=True
        self.scene_ready=True
        self.u.mem_write(b.BOOT_SITE,struct.pack('<I',b.branch(b.BOOT_SITE,b.CODE+self.marks['fc_boot_reset'])))
    def active(self,value):self.u.mem_write(0xc3074f10,bytes([value]))
    def ui_state(self,value):
        super().ui_state(value)
        if value in (4,5) and getattr(self,'scene_ready',False):
            # Firmware draws a fresh menu/playback surface before our callbacks.
            self.u.mem_write(v.PIX_FRONT,bytes(v.BUF))
            self.u.mem_write(v.PIX_UI,bytes(v.BUF))
    iso=800
    tick=0
    style=1
    task_result=7
    start_result=0
    creates=0
    starts=0
    sleeps=0
    def _code(self,u,address,size,data):
        if address==0xC002B6E0:self._ret(self.tick)
        elif address==0xC0057AE8:self._ret(v.CAMERA_IF)
        elif address==0xC005DBD8:self._ret(self.style)
        elif address==0xC0058B58:self._ret(self.iso)
        elif address==0xC0016A58:
            assert u.reg_read(v.ar.UC_ARM_REG_SP)%8==0
            ptr=u.reg_read(v.ar.UC_ARM_REG_R0)
            d=struct.unpack('<8I',u.mem_read(ptr,32))
            assert d[:5]==(0,0x41,b.CODE+self.marks['fc_worker'],28,0x2000),d
            assert d[7]==0
            self.creates+=1;self._ret(self.task_result)
        elif address==0xC0016BC0:
            assert u.reg_read(v.ar.UC_ARM_REG_SP)%8==0
            assert u.reg_read(v.ar.UC_ARM_REG_R0)==self.task_result
            assert u.reg_read(v.ar.UC_ARM_REG_R1)==0
            self.starts+=1;self._ret(self.start_result)
        elif address==0xC01F89C4:
            assert u.reg_read(v.ar.UC_ARM_REG_R0)==20
            assert u.reg_read(v.ar.UC_ARM_REG_SP)%8==0
            self.sleeps+=1
            # Stop one worker iteration at its yield point; no display submit.
            u.reg_write(v.ar.UC_ARM_REG_PC,v.STOP)
        elif address==0xC03A0750:
            self._ret(v.CAMERA_IF+0x40)
        elif address==0xC03A0798:
            super()._code(u,address,size,data)
            if self.auto_native:self.active(int(self.events[-1]==0x21))
        else:super()._code(u,address,size,data)
    def gesture(self,duration):
        self.call('fc_press',r0=v.CAMERA_IF)
        self.tick=(self.tick+duration)&0xffffffff
        self.call('fc_release',r0=v.CAMERA_IF)

def main():
    cases=0
    def prepare(start=0,ui=2,pref=None):
        if pref is None:pref=int(start==2)
        c=Camera();c.ui_state(ui);c.u.mem_write(b.STATE,struct.pack('<I',start))
        c.u.mem_write(b.STATE+0x18,struct.pack('<I',v.CAMERA_IF))
        c.active(int(start!=0));c.u.mem_write(b.STATE+0xB4,struct.pack('<I',pref))
        c.call('fc_worker');c.tick+=100000;c.call('fc_worker')
        return c
    def settle(c):
        c.call('fc_worker');c.tick+=100000;c.call('fc_worker');c.tick+=100000;c.call('fc_worker')
    def front(c):return bool(any(c.pixels(v.PIX_FRONT)))
    # Fix 14: (start, legend preference, duration, want, preference after).
    # Short from off restores the preference; short while on keeps it; only
    # a hold changes it.
    for start,pref,duration,want,after in [(0,0,100000,1,0),(0,1,100000,2,1),
                                           (1,0,100000,0,0),(1,1,100000,0,1),(2,1,100000,0,1),
                                           (0,0,500000,2,1),(0,1,500000,2,1),(1,0,500000,2,1),(2,1,500000,1,0),
                                           (0,0,499999,1,0),(0,0,500001,2,1)]:
        c=prepare(start,pref=pref);c.gesture(duration)
        assert c.state(0)==want and c.state(0xB4)==after,(start,pref,duration,c.state(0),c.state(0xB4))
        assert c.events==([0x22] if want==0 else [0x21] if start==0 else [])
        settle(c);c.ui_submit();assert bool(any(c.pixels()))==(want==2)
        if want==2:assert c.rows_written()==v.SCALE_ROWS
        cases+=1
    # Fix 14, the whole story from a fresh boot, for both styles: legend off at
    # first, a hold shows it, and each OFF/ON brings back what was last left.
    for style in (1,0):
        c=Camera();c.style=style;c.ui_state(2)
        c.u.mem_write(b.STATE+0x18,struct.pack('<I',v.CAMERA_IF))
        c.call('fc_init');c.u.mem_write(b.STATE+0x18,struct.pack('<I',v.CAMERA_IF))
        steps=[(100000,1),(500000,2),(100000,0),(100000,2),(500000,1),(100000,0),(100000,1),(100000,0),(100000,1)]
        for duration,want in steps:
            c.gesture(duration);settle(c)
            assert c.state(0)==want,(style,duration,want,c.state(0))
            assert front(c)==(want==2),(style,duration,want)
        cases+=1
    for start,want in [(0,2),(1,2),(2,1)]:
        for ui in (2,9):
            c=prepare(start,ui);c.call('fc_press',r0=v.CAMERA_IF);pressed=c.tick
            c.tick=pressed+499999;c.call('fc_worker');assert c.state(0)==start
            c.tick=pressed+500000;c.call('fc_worker');assert c.state(0)==want
            if start==0:
                assert not front(c)
                settle(c)
            assert front(c)==(want==2)
            events=list(c.events);c.tick+=700000;c.call('fc_worker')
            c.call('fc_release',r0=v.CAMERA_IF)
            assert c.state(0)==want and c.events==events;cases+=1
    # Gate starts when native activation is observed, not when request was sent.
    c=prepare();c.auto_native=False;c.gesture(600000);c.call('fc_worker')
    assert not front(c)
    c.tick+=80000;c.active(1);c.call('fc_worker');on=c.tick
    c.tick=on+99999;c.call('fc_worker');c.ui_submit();assert not front(c) and not any(c.pixels())
    c.tick=on+100000;c.call('fc_worker');assert front(c);cases+=1
    # Already-visible colours need no extra delay on a half-second scale hold.
    c=prepare(1);c.call('fc_press',r0=v.CAMERA_IF);c.tick+=500000;c.call('fc_worker')
    assert c.state(0)==2 and front(c);cases+=1
    # Fix 13: a camera that never starts gets five ON requests, then off.
    c=prepare();c.auto_native=False;c.gesture(100000)
    for _ in range(40):c.tick+=50000;c.call('fc_worker')
    assert c.state(0)==0 and not front(c) and c.events==[0x21]*5,c.events;cases+=1
    # Fix 13: the camera ignores the first request (still settling); the
    # re-sent one is taken and the toggle ends up on, no second press.
    c=prepare(pref=1);c.auto_native=False;c.gesture(100000);assert c.events==[0x21]
    c.tick+=299999;c.call('fc_worker');assert c.events==[0x21]
    c.tick+=1;c.call('fc_worker');assert c.events==[0x21,0x21] and c.state(0)==2
    c.active(1);settle(c);assert c.state(0)==2 and front(c) and c.state(0xD8)==0
    c.tick+=2000000;c.call('fc_worker');assert c.events==[0x21,0x21];cases+=1
    # The same after the menu: the recovery request is ignored once.
    c=prepare(2);c.ui_state(5);c.call('fc_worker');c.active(0);c.call('fc_worker')
    c.auto_native=False;c.ui_state(2);c.call('fc_worker');c.tick+=100000;c.call('fc_worker')
    assert c.events==[0x21]
    c.tick+=300000;c.call('fc_worker');assert c.events==[0x21,0x21]
    c.active(1);settle(c);assert c.state(0)==2 and front(c);cases+=1
    # A quick second tap cancels an outstanding native-start request.
    c=prepare();c.auto_native=False;c.gesture(100000);c.gesture(100000)
    assert c.state(0)==0 and c.events==[0x21,0x22];cases+=1
    for start in (1,2):
        for ui in (0,1,3,4,5,6,7,8,10):
            c=prepare(start);assert front(c)==(start==2)
            c.ui_state(ui);c.call('fc_worker');assert c.state(0)==start and not front(c)
            c.ui_state(2);settle(c);assert front(c)==(start==2)
            assert c.state(0)==start and c.state(0xB4)==int(start==2);cases+=1
        # Native colours can stop in the menu and return later without a key.
        c=prepare(start);c.ui_state(5);c.call('fc_worker');c.active(0);c.call('fc_worker')
        c.ui_state(2);c.call('fc_worker');assert not front(c)
        c.active(1);settle(c);assert front(c)==(start==2) and c.state(0)==start;cases+=1
    # Fix 12: style 0 (False Color) keeps the scale and swaps the legend.
    c=prepare(2);c.ui_submit();el=c.pixels()
    c.style=0;settle(c);c.ui_submit();fc=c.pixels()
    assert c.state(0)==2 and front(c) and c.state(0xD0)==0 and fc!=el
    c.style=1;settle(c);c.ui_submit();assert c.state(0)==2 and front(c) and c.pixels()==el
    # Any other style value still has no scale.
    c.style=2;c.call('fc_worker');assert not front(c)
    c.style=1;settle(c);assert c.state(0)==2 and front(c);cases+=1
    # AF stops colours before watcher runs; the next tap must request ON once.
    for start in (1,2):
        c=prepare(start);c.active(0);c.gesture(100000)
        assert c.state(0)==(2 if start==2 else 1) and c.events==[0x21],(start,c.events);cases+=1
    # AF stop concurrent with a fresh press must not swallow its release/hold.
    for duration,want in ((100000,2),(500000,2)):
        c=prepare(2);c.call('fc_press',r0=v.CAMERA_IF);c.active(0);c.call('fc_worker')
        assert c.state(0x10)==1 and c.state(0)==0
        c.tick+=duration;c.call('fc_worker');c.call('fc_release',r0=v.CAMERA_IF)
        assert c.state(0)==want and c.events==[0x21];settle(c);assert front(c)==(want==2);cases+=1
    c=prepare(2);c.ui_state(5);c.call('fc_worker');c.active(0);c.call('fc_worker')
    c.ui_state(2);c.gesture(100000);assert c.state(0)==2 and c.events==[0x21];cases+=1
    for style in (2,0xffffffff):
        c=prepare();c.style=style;c.gesture(600000)
        assert c.state(0)==1 and c.events==[0x21] and not front(c);cases+=1
    # False Color style gets the same gestures as EL Zone: short = colours +
    # scale, half-second hold toggles only the scale.
    for start,duration,want in [(0,100000,1),(2,100000,0),(1,500000,2),(2,500000,1)]:
        c=prepare(start);c.style=0;c.gesture(duration);assert c.state(0)==want,(start,duration)
        settle(c);assert front(c)==(want==2);cases+=1
    c=prepare();c.call('fc_release',r0=v.CAMERA_IF);assert not c.events;cases+=1
    c=prepare();c.call('fc_press',r0=v.CAMERA_IF);c.tick+=300000
    c.call('fc_press',r0=v.CAMERA_IF);c.tick+=200000;c.call('fc_worker')
    assert c.state(0)==2 and c.events==[0x21] and c.creates==1;cases+=1
    c=prepare();c.tick=0xfffc0000;c.call('fc_press',r0=v.CAMERA_IF)
    c.tick=(c.tick+500000)&0xffffffff;c.call('fc_worker');assert c.state(0)==2
    c.call('fc_worker');c.tick=(c.tick+100000)&0xffffffff;c.call('fc_worker');assert front(c);cases+=1
    # Same code persists across boots; run the actual patched CameraIF constructor.
    c=prepare(2);c.call('fc_start');assert c.creates==1
    for epoch in range(3):
        result=c._run(0xc03716f0,r0=v.CAMERA_IF)
        assert result==v.CAMERA_IF
        assert bytes(c.u.mem_read(b.STATE,b.STATE_SIZE))==bytes(b.STATE_SIZE)
        assert c.creates==epoch+1       # no spawning during construction
        c.active(0);c.task_result=8+epoch
        c.call('fc_press',r0=v.CAMERA_IF)
        assert c.creates==epoch+2 and c.starts==epoch+2
        c.tick+=500000;c.call('fc_worker');assert c.state(0)==2
        settle(c);assert front(c)
        c.call('fc_release',r0=v.CAMERA_IF);assert c.state(0)==2
        cases+=1
    c=Camera();c.call('fc_start');c.call('fc_start');assert c.creates==1 and c.starts==1;cases+=1
    c=Camera();c.task_result=-1;c.call('fc_start');assert c.starts==0
    c.task_result=7;c.call('fc_start');assert c.creates==2 and c.starts==1;cases+=1
    c=Camera();c.start_result=-5;c.call('fc_start');assert c.state(0x88)==0xfffffffb
    c.start_result=0;c.call('fc_start');assert c.creates==1 and c.starts==2;cases+=1
    c=prepare(2);c.ui_submit();pixels=c.pixels()
    assert c.rows_written()==v.SCALE_ROWS
    assert all(not p or (43<=i%v.W<980 and 556<=i//v.W<629) for i,p in enumerate(pixels));cases+=1
    # Compare every pixel: Fix 13 keeps native band colours at every ISO;
    # only the labels strictly after clipping turn red.
    c=prepare(2);c.ui_submit();normal=c.pixels()
    variants={0:normal}
    for iso,index,variant in ((100,11,1),(400,13,2)):
        red_x=struct.unpack('<H',c.u.mem_read(b.CODE+c.marks['labels']+(index+1)*4,2))[0]
        c.iso=iso;c.call('fc_worker');c.ui_submit();special=c.pixels()
        assert c.state(0xC0)==iso and c.state(0xBC)==variant
        for y in range(v.H):
            for x in range(v.W):
                i=y*v.W+x
                expected=normal[i]
                if 559<=y<581 and x>=red_x and normal[i]==0xffff:
                    expected=0xff00
                assert special[i]==expected,(iso,x,y,hex(special[i]),hex(expected))
        variants[variant]=special;cases+=1
    for iso in (100,200,400,800,1600,3200,6400,12800,25600,51200,102400,125,0,0xffffffff):
        c.iso=iso;c.call('fc_worker');c.ui_submit()
        variant=1 if iso in (100,200) else 2 if iso in (400,3200) else 0
        assert c.pixels()==variants[variant],iso;cases+=1
    # Same translucent backdrop: 8px sides, top padding, no strip below bands.
    for y in range(556,629):
        assert all(normal[y*v.W+x]==0x8000 for x in (*range(43,51),*range(972,980)))
    for y in (556,557,558):
        assert all(normal[y*v.W+x]==0x8000 for x in range(43,980))
    assert normal[585*v.W+500]==0x8000
    assert normal[628*v.W+51:628*v.W+972]==normal[590*v.W+51:590*v.W+972]
    assert not any(normal[629*v.W:630*v.W]);cases+=1
    # Suspended native flag can remain stale-high: reissue ON once on menu return.
    for native in (0,1):
        c=prepare(2);c.ui_state(5);c.call('fc_worker');c.active(native)
        c.ui_state(2);c.call('fc_worker');assert not c.events
        c.tick+=99999;c.call('fc_worker');assert not c.events
        c.tick+=1;c.call('fc_worker');assert c.events==[0x21]
        settle(c);assert front(c)
        c.call('fc_worker');assert c.events==[0x21];cases+=1
    # A user tap cancels/supersedes pending return recovery without a later extra ON.
    c=prepare(2);c.ui_state(5);c.call('fc_worker');c.ui_state(2);c.call('fc_worker')
    c.gesture(100000);settle(c);assert c.events==[0x22] and c.state(0)==0;cases+=1
    # The shorter loader explicitly clears all state through the real init routine.
    c=Camera();c.u.mem_write(b.STATE,b'\xff'*b.STATE_SIZE);c.call('fc_init')
    assert bytes(c.u.mem_read(b.STATE,b.STATE_SIZE))==bytes(b.STATE_SIZE);cases+=1
    # Pixel-exact menu preservation, including drawing into a previously used buffer.
    for ui in (4,5):
        c=prepare(2);c.ui_submit();c.ui_state(ui)
        pattern=struct.pack('<H',0xf345)*(v.W*v.H)
        c.u.mem_write(v.PIX_FRONT,pattern);c.u.mem_write(v.PIX_UI,pattern)
        c.writes.clear();c.call('fc_worker');assert not c.writes
        assert bytes(c.u.mem_read(v.PIX_FRONT,v.BUF))==pattern
        c.ui_submit();assert not c.writes and bytes(c.u.mem_read(v.PIX_UI,v.BUF))==pattern;cases+=1
    # The False Color legend, checked against the firmware table read here
    # directly (0xC0D1B7C4, all 180 entries, not the builder's merged runs).
    image=b.MAIN.read_bytes();pal=b.palette(image)
    c=prepare(2);c.style=0;settle(c);c.ui_submit();fcpix=c.pixels()
    assert c.rows_written()==v.SCALE_ROWS
    assert all(not p or (43<=i%v.W<980 and 556<=i//v.W<629) for i,p in enumerate(fcpix))
    expected_band={}
    for x0,x1,index in b.bands(image,b.FC_BAND_TABLE,b.FC_BAND_N_RAW):
        for x in range(51+x0*9//10,51+(x1+1)*9//10):
            expected_band[x]=b.pack16(b.ayuv_to_rgb(pal[index]))
    for y in range(590,629):
        for x in range(43,980):
            want=expected_band.get(x,0x8000)
            assert fcpix[y*v.W+x]==want,(x,y,hex(fcpix[y*v.W+x]),hex(want))
    # Labels: only white ink, only inside a label's glyph box, and every
    # one of the fifteen glyphs inks something.
    names=[n for n,_ in b.GLYPHS]
    boxes=[]
    for x,name in b.FC_LABELS:
        gx=51+x*9//10
        hdr=c.u.mem_read(b.CODE+c.marks['glyph_hdr']+names.index(name)*4,4)
        boxes.append((gx,gx+hdr[2]))
    for y in range(556,590):
        for x in range(43,980):
            p=fcpix[y*v.W+x]
            assert p in (0x8000,0xffff),(x,y,hex(p))
            if p==0xffff:assert 559<=y<559+b.GLYPH_H*9//10 and any(a<=x<e for a,e in boxes),(x,y)
    for a,e in boxes:
        assert any(fcpix[y*v.W+x]==0xffff for y in range(559,581) for x in range(a,e)),(a,e)
    # No ISO variants on the False Color legend.
    for iso in (100,400,800,3200):
        c.iso=iso;c.call('fc_worker');c.ui_submit();assert c.pixels()==fcpix,iso
    cases+=1
    v.render(variants[1],b.ROOT/'builds/fcscale/scale-iso100.png')
    v.render(variants[2],b.ROOT/'builds/fcscale/scale-iso400.png')
    v.render(pixels,b.ROOT/'builds/fcscale/scale.png')
    v.render(fcpix,b.ROOT/'builds/fcscale/scale-falsecolor.png')
    print(f'PASS: {cases} Fix 15 safe load, remembered legend, EL Zone + False Color legends, universal EL colours with red clipping labels, ON retry, flush bottom, 8-pixel sides and gesture/menu/reboot scenarios; real transitions still need camera testing.',flush=True)
if __name__=='__main__':main()

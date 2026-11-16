"""Run Rust FFI calls through the same TEE model used by lang/c."""
from pathlib import Path
import json
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "tools/rust2maude.py"


class TeePipelineTest(unittest.TestCase):
    def evaluate(self, source, expected, success=True, options=()):
        with tempfile.TemporaryDirectory(prefix="tee-rust-") as work:
            path = Path(work) / "input.rs"
            path.write_text(source)
            result = subprocess.run([sys.executable, str(RUNNER), str(path), "--function", "run",
                                     "--output-dir", str(Path(work) / "out"), "--timeout", "20", *options],
                                    capture_output=True, text=True, timeout=40)
            log = Path(work) / "out/maude.log"
            detail = result.stdout + result.stderr + (log.read_text()[-12000:] if log.exists() else "")
            self.assertEqual(result.returncode == 0, success, detail)
            self.assertIn(expected, result.stdout + result.stderr, detail)
            return result.stdout + result.stderr

    def test_every_c_api_has_a_rust_signature(self):
        c = (ROOT / "lang/c/tee-support.maude").read_text()
        c_apis = set(re.findall(r"eq getFuncParams\((\w+)\)", c)) - {"P", "FID", "TF"}
        rust = set(re.findall(r"eq rustSig\((\w+)\)", (ROOT / "lang/rust/tee-support.maude").read_text()))
        self.assertEqual(c_apis - rust, set())
        self.assertEqual(len(c_apis), 29)

    def test_allocate_reset_info_free(self):
        self.evaluate('''
        #[repr(C)] struct Info { kind:u32, size:u32, max:u32, usage:u32, data:u32, pos:u32, flags:u32 }
        extern "C" {
            fn allocateTransientObject(t:u32,n:u32,out:*mut usize)->u32;
            fn resetTransientObject(h:usize);
            fn getObjectInfo1(h:usize,out:*mut Info)->u32;
            fn freeTransientObject(h:usize);
        }
        pub fn run()->u32 {
            let mut h=0usize;
            let mut info=Info{kind:0,size:0,max:0,usage:0,data:0,pos:0,flags:0};
            unsafe {
                allocateTransientObject(0xA0000010,128,&mut h);
                resetTransientObject(h);
                getObjectInfo1(h,&mut info);
                freeTransientObject(h);
            }
            info.max
        }
        ''', "evaluated(Integer(128, 32, false))")

    def test_allocation_error(self):
        self.evaluate('''extern "C" { fn allocateTransientObject(t:u32,n:u32,out:*mut usize)->u32; }
            pub fn run()->u32 { let mut h=0usize; unsafe { allocateTransientObject(0xA0000010,127,&mut h) } }
        ''', "evaluated(Integer(4294901770, 32, false))")

    def test_system_time_fields(self):
        self.evaluate('''#[repr(C)] struct Time {seconds:u32,millis:u32}
            extern "C" { fn getSystemTime(out:*mut Time); }
            pub fn run()->u32 { let mut t=Time{seconds:99,millis:99}; unsafe { getSystemTime(&mut t) }; t.seconds+t.millis }
        ''', "evaluated(Integer(0, 32, false))")

    def test_random_buffer(self):
        self.evaluate('''extern "C" { fn generateRandom(out:*mut [u8;1]); }
            pub fn run()->[u8;1] { let mut b=[0u8]; unsafe { generateRandom(&mut b) }; b }
        ''', "evaluated(# randomData)")

    def test_operation_allocate_free(self):
        self.evaluate('''extern "C" {
            fn allocOperation(a:u32,m:u32,n:u32,out:*mut usize)->u32;
            fn resetOperation(h:usize); fn freeOperation(h:usize);
        }
        pub fn run()->u32 { let mut h=0usize; let r=unsafe{allocOperation(0x30000004,4,256,&mut h)};
            unsafe { freeOperation(h); } r }
        ''', "evaluated(Integer(0, 32, false))")

    def test_reset_without_key_panics(self):
        self.evaluate('''extern "C" {
            fn allocOperation(a:u32,m:u32,n:u32,out:*mut usize)->u32;
            fn resetOperation(h:usize);
        }
        pub fn run()->u32 { let mut h=0usize; unsafe { allocOperation(0x30000004,4,256,&mut h); resetOperation(h); } 0 }
        ''', "TEE execution panicked", success=False)

    def test_attributes_key_and_mac_roundtrip(self):
        self.evaluate('''extern "C" {
            fn allocateTransientObject(t:u32,n:u32,out:*mut usize)->u32;
            fn initRefAttribute(id:u32,data:*const [u8;32],out:*mut (u32,u32));
            fn populateTransientObject(h:usize,attrs:*const (u32,u32))->u32;
            fn allocOperation(a:u32,m:u32,n:u32,out:*mut usize)->u32;
            fn setOperationKey(op:usize,key:usize)->u32;
            fn resetOperation(op:usize);
            fn macInit(op:usize,iv:*const [u8;1]);
            fn macUpdate(op:usize,msg:*const [u8;1]);
            fn macComputeFinal(op:usize,msg:*const [u8;1],out:*mut [u8;1])->u32;
            fn macCompareFinal(op:usize,msg:*const [u8;1],mac:*const [u8;1])->u32;
            fn freeOperation(h:usize); fn freeTransientObject(h:usize);
        }
        pub fn run()->u32 {
            let keydata=[1u8;32]; let mut attr=(0u32,0u32); let mut key=0usize; let mut op=0usize;
            let iv=[0u8]; let msg=[7u8]; let chunk=[2u8]; let mut mac=[0u8];
            let rc;
            unsafe {
                allocateTransientObject(0xA0000004,256,&mut key);
                initRefAttribute(0xC0000000,&keydata,&mut attr);
                populateTransientObject(key,&attr);
                allocOperation(0x30000004,4,256,&mut op);
                setOperationKey(op,key); resetOperation(op);
                macInit(op,&iv); macUpdate(op,&chunk); macComputeFinal(op,&msg,&mut mac);
                macInit(op,&iv); macUpdate(op,&chunk); rc=macCompareFinal(op,&msg,&mac);
                freeOperation(op); freeTransientObject(key);
            }
            rc
        }
        ''', "evaluated(Integer(0, 32, false))")

    def test_persistent_create_write_seek_read_rename_delete(self):
        self.evaluate('''extern "C" {
            fn createPersistent(storage:u32,id:*const u32,flags:u32,key:usize,data:*const [u8;2],size:u32,out:*mut usize)->u32;
            fn writeObjectData(h:usize,data:*const [u8;2])->u32;
            fn seekObjectData(h:usize,offset:i32,whence:u32)->u32;
            fn readObjData(h:usize,size:u32,out:*mut [u8;2])->u32;
            fn truncateObjectData(h:usize,size:u32)->u32;
            fn renamePersistentObject(h:usize,id:*const u32)->u32;
            fn closeAndDeletePersistentObject1(h:*mut usize)->u32;
        }
        pub fn run()->u32 {
            let id=7u32; let newid=8u32; let data=[3u8,4u8]; let mut out=[0u8;2]; let mut h=0usize;
            let rc;
            unsafe {
                createPersistent(1,&id,7,0,&data,2,&mut h);
                writeObjectData(h,&data); seekObjectData(h,0,0); readObjData(h,2,&mut out);
                truncateObjectData(h,1); renamePersistentObject(h,&newid);
                rc=closeAndDeletePersistentObject1(&mut h);
            }
            rc
        }
        ''', "evaluated(Integer(0, 32, false))")

    def test_internal_session_shared_output(self):
        self.evaluate('''extern "C" {
            fn TeeOpenTASession(app:u32,timeout:u32,out:*mut usize,origin:*mut u32)->u32;
            fn TeeInvokeTACommand(session:usize,command:u32,params:(u32,u32,*mut u32),origin:*mut u32)->u32;
        }
        pub fn cmd_0(a:u32,b:u32,out:&mut u32) { *out=a+b; }
        pub fn run()->u32 {
            let mut session=0usize; let mut origin=0u32; let mut out=0u32;
            unsafe {
                TeeOpenTASession(42,0,&mut session,&mut origin);
                TeeInvokeTACommand(session,0,(10,12,&mut out),&mut origin);
            }
            out
        }
        ''', "evaluated(Integer(22, 32, false))", options=("--ta", "42"))

    def test_internal_session_missing_target(self):
        self.evaluate('''extern "C" {
            fn TeeOpenTASession(app:u32,timeout:u32,out:*mut usize,origin:*mut u32)->u32;
        }
        pub fn run()->u32 { let mut session=0usize; let mut origin=0u32;
            unsafe { TeeOpenTASession(42,0,&mut session,&mut origin) } }
        ''', "evaluated(Integer(4294901768, 32, false))")

    def test_ree_open_invoke_shared_output_close(self):
        self.evaluate('''extern "C" {
            fn teecOpenSession(app:u32,out:*mut usize)->u32;
            fn teecInvokeCommand(session:usize,command:u32,params:(u32,u32,*mut u32))->u32;
            fn teecCloseSession(session:usize)->u32;
        }
        pub fn cmd_0(a:u32,b:u32,out:&mut u32) { *out=a+b; }
        pub fn run()->u32 { let mut session=0usize; let mut out=0u32;
            unsafe {
                teecOpenSession(42,&mut session);
                teecInvokeCommand(session,0,(10,12,&mut out));
                teecCloseSession(session);
            }
            out
        }
        ''', "evaluated(Integer(22, 32, false))", options=("--ta", "42", "--role", "ra"))

    def test_persistent_close_and_reopen(self):
        self.evaluate('''extern "C" {
            fn createPersistent(storage:u32,id:*const u32,flags:u32,key:usize,data:*const [u8;1],size:u32,out:*mut usize)->u32;
            fn closeObject(h:usize);
            fn openPersistentObject(storage:u32,id:*const u32,flags:u32,out:*mut usize)->u32;
        }
        pub fn run()->u32 { let id=5u32; let data=[6u8]; let mut h=0usize;
            unsafe { createPersistent(1,&id,7,0,&data,1,&mut h); closeObject(h);
                openPersistentObject(1,&id,7,&mut h) }
        }
        ''', "evaluated(Integer(0, 32, false))")

    def test_cipher_init_update(self):
        self.evaluate('''extern "C" {
            fn allocateTransientObject(t:u32,n:u32,out:*mut usize)->u32;
            fn initRefAttribute(id:u32,data:*const [u8;16],out:*mut (u32,u32));
            fn populateTransientObject(h:usize,attrs:*const (u32,u32))->u32;
            fn allocOperation(a:u32,m:u32,n:u32,out:*mut usize)->u32;
            fn setOperationKey(op:usize,key:usize)->u32;
            fn cipherInit(op:usize,iv:usize);
            fn cipherUpdate(op:usize,src:*const [u8;1],out:*mut [u8;1])->u32;
            fn freeOperation(h:usize); fn freeTransientObject(h:usize);
        }
        pub fn run()->u32 {
            let keydata=[1u8;16]; let mut attr=(0u32,0u32); let mut key=0usize; let mut op=0usize;
            let msg=[7u8]; let mut out=[0u8]; let rc;
            unsafe {
                allocateTransientObject(0xA0000010,128,&mut key);
                initRefAttribute(0xC0000000,&keydata,&mut attr); populateTransientObject(key,&attr);
                allocOperation(0x10000110,0,128,&mut op); setOperationKey(op,key);
                cipherInit(op,0); rc=cipherUpdate(op,&msg,&mut out);
                freeOperation(op); freeTransientObject(key);
            }
            rc
        }
        ''', "evaluated(Integer(0, 32, false))")

    def test_separately_compiled_ta(self):
        with tempfile.TemporaryDirectory(prefix="tee-target-") as work:
            target = Path(work) / "service.rs"
            target.write_text("pub fn cmd_3(n:u32,out:&mut u32) { *out=n+4; }")
            self.evaluate('''extern "C" {
                fn TeeOpenTASession(app:u32,timeout:u32,out:*mut usize,origin:*mut u32)->u32;
                fn TeeInvokeTACommand(session:usize,command:u32,params:(u32,*mut u32),origin:*mut u32)->u32;
            }
            pub fn run()->u32 { let mut session=0usize; let mut origin=0u32; let mut out=0u32;
                unsafe { TeeOpenTASession(42,0,&mut session,&mut origin);
                    TeeInvokeTACommand(session,3,(10,&mut out),&mut origin); } out }
            ''', "evaluated(Integer(14, 32, false))", options=("--ta", f"42={target}"))

    def test_panicked_ta_preserves_shared_output(self):
        result = self.evaluate('''extern "C" {
            fn TeeOpenTASession(app:u32,timeout:u32,out:*mut usize,origin:*mut u32)->u32;
            fn TeeInvokeTACommand(session:usize,command:u32,params:(*mut u32,),origin:*mut u32)->u32;
            fn allocOperation(a:u32,m:u32,n:u32,out:*mut usize)->u32;
            fn resetOperation(handle:usize);
        }
        pub fn cmd_0(out:&mut u32) { let mut op=0usize; *out=55;
            unsafe { allocOperation(0x30000004,4,256,&mut op); resetOperation(op); } }
        pub fn run()->(u32,u32,u32) {
            let mut session=0usize; let mut origin=0u32; let mut out=99u32; let rc;
            unsafe { TeeOpenTASession(42,0,&mut session,&mut origin);
                rc=TeeInvokeTACommand(session,0,(&mut out,),&mut origin); }
            (rc,out,origin)
        }
        ''', "Integer(4294914084, 32, false)", options=("--ta", "42"))
        self.assertIn("Integer(99, 32, false)", result)
        self.assertIn("Integer(3, 32, false)", result)


if __name__ == "__main__":
    unittest.main()

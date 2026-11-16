"""Compiler-to-Maude integration tests. Requires rustc and Maude, no pip packages."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "tools/rust2maude.py"


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rust-maude-test-")
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)

    def evaluate(self, source, function, *args, success=True):
        if isinstance(source, str):
            path = self.work / "input.rs"
            path.write_text(source)
        else:
            path = source
        command = [sys.executable, str(RUNNER), str(path),
                   "--output-dir", str(self.work / "artifacts"), "--timeout", "15"]
        if function is not None:
            command.extend(["--function", function])
        for arg in args:
            command.append(f"--arg={arg}")
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def test_changed_source_matches_native_rust(self):
        source = """fn fibonacci(n: u32) -> u32 {
            match n { 0 => 2, 1 => 3, _ => fibonacci(n-1) + fibonacci(n-2) }
        }
        pub fn run() -> u32 { fibonacci(7) }
        fn main() { println!(\"{}\", run()); }
        """
        result = self.evaluate(source, "run")
        native = self.work / "native"
        subprocess.run(["rustc", str(self.work / "input.rs"), "-o", str(native)],
                       check=True, capture_output=True, timeout=20)
        expected = subprocess.run([str(native)], check=True, capture_output=True,
                                  text=True, timeout=5).stdout.strip()
        self.assertIn(f"evaluated(Integer({expected}, 32, false))", result)

    def test_multiple_functions_signed_arguments_and_branch(self):
        result = self.evaluate("""fn plus(a: i32, b: i32) -> i32 { a + b }
            fn choose(n: i32, b: bool) -> i32 {
                if b { plus(n, 7) } else { plus(n, 2) }
            }""", "choose", -5, "true")
        self.assertIn("evaluated(Integer(2, 32, true))", result)

    def test_tuple_return(self):
        self.assertIn("evaluated(Aggregate(", self.evaluate(
            "fn pair(n: u32) -> (u32, bool) { (n, n > 3) }", "pair", 4))

    def test_overflow_fails(self):
        result = self.evaluate("fn add(n: u32) -> u32 { n + 1 }", "add", 4294967295, success=False)
        self.assertIn("MIR execution failed", result)
        self.assertIn("AssertError", result)

    def test_unsupported_reachable_code_fails(self):
        result = self.evaluate("fn greeting() { println!(\"hello\"); }", "greeting", success=False)
        self.assertIn("Unsupported MIR", result)

    def test_argument_count_and_range(self):
        source = "fn identity(n: u8) -> u8 { n }"
        self.assertIn("expects 1 argument", self.evaluate(source, "identity", success=False))
        self.assertIn("outside u8", self.evaluate(source, "identity", 256, success=False))

    def test_compiler_failure(self):
        self.assertIn("rustc failed", self.evaluate("fn invalid( {", "invalid", success=False))



if __name__ == "__main__":
    unittest.main()

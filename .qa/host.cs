using System;
using System.Runtime.InteropServices;
public class AnkiPythonHost {
 [DllImport("kernel32.dll", CharSet=CharSet.Unicode)] static extern bool SetDllDirectory(string path);
 [DllImport("C:\\Program Files\\Anki\\python313.dll", CallingConvention=CallingConvention.Cdecl, CharSet=CharSet.Unicode)] static extern void Py_SetPath(string path);
 [DllImport("C:\\Program Files\\Anki\\python313.dll", CallingConvention=CallingConvention.Cdecl)] static extern void Py_Initialize();
 [DllImport("C:\\Program Files\\Anki\\python313.dll", CallingConvention=CallingConvention.Cdecl)] static extern int PyRun_SimpleString(byte[] command);
 public static int Main(string[] args) {
  SetDllDirectory(@"C:\Program Files\Anki");
  Py_SetPath(@"C:\Program Files\Anki\python313.zip;C:\Program Files\Anki;C:\Program Files\Anki\app_packages;" + Environment.CurrentDirectory);
  Environment.SetEnvironmentVariable("ADF_QA_SCRIPT", args[0]);
  Py_Initialize();
  return PyRun_SimpleString(System.Text.Encoding.UTF8.GetBytes("import os; exec(compile(open(os.environ['ADF_QA_SCRIPT'], encoding='utf-8-sig').read(), os.environ['ADF_QA_SCRIPT'], 'exec'))\n\0"));
 }
}

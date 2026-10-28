using System;
using System.Diagnostics;

internal static class OrbitHiveNewProductLauncher
{
    private static void Main()
    {
        var start = new ProcessStartInfo
        {
            FileName = @"C:\Users\Windows11\AppData\Local\Programs\Python\Python39\python.exe",
            Arguments = "tools\\run_console.py",
            WorkingDirectory = @"C:\Users\Windows11\Documents\Codex\2026-06-26\orbit-hive-codex-github-kylebit-orbit\work\tiktok_e_comm",
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden
        };
        start.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
        Process.Start(start);
    }
}

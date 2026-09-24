@echo off
rem Build the extractor on Windows with the C# compiler that ships with .NET Framework 4.x.
cd /d "%~dp0"
if not exist bin mkdir bin
set CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe
if not exist "%CSC%" set CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe
"%CSC%" /nologo /out:bin\TerrariaExtractor.exe TerrariaExtractor.cs Probing.cs || exit /b 1
"%CSC%" /nologo /platform:x86 /out:bin\TerrariaExtractor32.exe TerrariaExtractor.cs Probing.cs || exit /b 1
echo built bin\TerrariaExtractor.exe and bin\TerrariaExtractor32.exe

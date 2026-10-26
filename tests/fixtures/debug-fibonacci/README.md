# Existing sample and disposable regression driver

`FibonacciGenerator.cs` is unchanged from the MIT-licensed [dotnet/samples FibonacciBetterMsBuild example](https://github.com/dotnet/samples/tree/ab42c8a4251cb90787aa5d3c94b78d28d85bc46b/core/console-apps/FibonacciBetterMsBuild), pinned at `ab42c8a4251cb90787aa5d3c94b78d28d85bc46b`. The upstream license is retained as `LICENSE`.

For this debugging check, `Program.cs` requests 48 terms instead of the original 15, asserts term 47 equals `2971215073L`, and exits 1 on mismatch. `Fibonacci.csproj` targets installed .NET 8 with portable debug symbols and optimization disabled. The existing generator overflows its 32-bit values at this boundary. This fixture deliberately retains that bug; an agent fixes a disposable copy, never this baseline.

Reproduce with `dotnet run --project tests/fixtures/debug-fibonacci`: the baseline prints `-1323752223` and exits 1. The generator is a real upstream example; the assertion/driver and framework retarget are test preparation, not an undiscovered production incident.

using System;
using System.Linq;
namespace Hello
{
    class Program
    {
        static void Main()
        {
            var generator = new FibonacciGenerator();
            var terms = generator.Generate(48).ToArray();
            long actual = terms[47];
            Print(actual);
            if (actual != 2971215073L) Environment.ExitCode = 1;
        }
        static void Print(long actual)
        {
            Console.WriteLine(actual);
        }
    }
}

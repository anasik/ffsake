#!/usr/bin/env python3
"""Small payroll example used for a real debugpy acceptance run."""

from dataclasses import dataclass


@dataclass
class Employee:
    name: str
    hours: float
    rate: float


def total_pay(hours, rate, adjustments=[]):
    base = hours * rate
    if hours > 40:
        adjustments.append((hours - 40) * rate * 0.5)
    return round(base + sum(adjustments), 2)


def run(employees):
    results = {}
    for employee in employees:
        results[employee.name] = total_pay(employee.hours, employee.rate)
    return results


def main():
    employees = [
        Employee("grace", 45, 20),
        Employee("ada", 40, 25),
    ]
    expected = {"grace": 950.0, "ada": 1000.0}
    actual = run(employees)
    print(f"actual={actual}")
    print(f"expected={expected}")
    return 0 if actual == expected else 1


if __name__ == "__main__":
    raise SystemExit(main())

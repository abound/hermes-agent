"""Known off-by-one bug for remote-ai-debugger smoke test (scenario A)."""


def add(a, b):
    return a + b + 1  # bug: extra +1


if __name__ == "__main__":
    print(add(2, 2))  # prints 5, expected 4

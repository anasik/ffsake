#include <iostream>

struct Node {
    int value;
    Node* next;
};

void append(Node*& head, Node*& tail, int value) {
    Node* node = new Node{value, nullptr};
    if (tail) {
        tail->next = node;
    }
    tail = node;
}

int sum(const Node* head) {
    int total = 0;
    for (const Node* node = head; node; node = node->next) {
        total += node->value;
    }
    return total;
}

int main() {
    Node* head = nullptr;
    Node* tail = nullptr;
    for (int value = 1; value <= 5; ++value) {
        append(head, tail, value);
    }
    const int total = sum(head);
    std::cout << "sum=" << total << " count=5" << std::endl;
    return total == 15 ? 0 : 1;
}

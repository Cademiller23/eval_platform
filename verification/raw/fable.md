=== RESPONSE coh-essay ===
Biodiversity, the variety of life on Earth at the level of genes, species and ecosystems, matters first because it underpins the systems that keep us alive. Pollinators fertilise roughly a third of the crops we eat, forests and wetlands filter water and regulate rainfall, and soil organisms recycle nutrients so that plants can grow. These services are not luxuries; they are the quiet infrastructure of every economy, and no technology yet exists that could replace them at scale.

Biodiversity also provides resilience. A diverse ecosystem is like a diversified investment portfolio: when one species declines through disease, drought or a changing climate, others can fill its role and the system continues to function. Monocultures, by contrast, are fragile. The Irish potato famine and the near-collapse of the Gros Michel banana show how dangerous it is to depend on a narrow genetic base. Wild relatives of crops, and the genetic variety within species, are the raw material from which future food security and new medicines will be drawn.

Finally, biodiversity has value beyond its usefulness to people. Each species is the product of millions of years of evolution and cannot be recreated once it is gone. Many cultures find meaning, identity and beauty in the living world around them, and there is a strong ethical argument that we have no right to extinguish other forms of life for short-term convenience. Protecting biodiversity is therefore both an act of self-preservation and an expression of responsibility toward the planet we share.
=== END ===

=== RESPONSE coh-memory ===
Your name is Priya, and your favourite colour is teal.
=== END ===

=== RESPONSE coh-stability ===
2, 3, 5, 7, 11
=== END ===

=== RESPONSE coh-multilingual ===
French: Bonjour
German: Hallo
Japanese: こんにちは
Hindi: नमस्ते
=== END ===

=== RESPONSE coh-count ===
1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60
=== END ===

=== RESPONSE coh-stop ===
OK
=== END ===

=== RESPONSE coh-needle ===
7391
=== END ===

=== RESPONSE code-palindrome ===
```python
def is_palindrome(s: str) -> bool:
    cleaned = [c.lower() for c in s if c.isalnum()]
    return cleaned == cleaned[::-1]
```
=== END ===

=== RESPONSE code-twosum ===
```python
def two_sum(nums: list[int], target: int) -> list[int]:
    seen = {}
    for j, num in enumerate(nums):
        complement = target - num
        if complement in seen:
            return [seen[complement], j]
        seen[num] = j
    return []
```
=== END ===

=== RESPONSE code-intervals ===
```python
def merge_intervals(intervals: list[list[int]]) -> list[list[int]]:
    if not intervals:
        return []
    intervals = sorted(intervals, key=lambda x: x[0])
    merged = [list(intervals[0])]
    for start, end in intervals[1:]:
        last = merged[-1]
        if start <= last[1]:
            last[1] = max(last[1], end)
        else:
            merged.append([start, end])
    return merged
```
=== END ===

=== RESPONSE code-roman ===
```python
def roman_to_int(s: str) -> int:
    values = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
    total = 0
    for i, ch in enumerate(s):
        value = values[ch]
        if i + 1 < len(s) and values[s[i + 1]] > value:
            total -= value
        else:
            total += value
    return total
```
=== END ===

=== RESPONSE code-brackets ===
```python
def valid_parentheses(s: str) -> bool:
    pairs = {')': '(', ']': '[', '}': '{'}
    stack = []
    for ch in s:
        if ch in '([{':
            stack.append(ch)
        elif ch in pairs:
            if not stack or stack.pop() != pairs[ch]:
                return False
    return not stack
```
=== END ===

=== RESPONSE code-lru ===
```python
from collections import OrderedDict


class LRUCache:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.cache = OrderedDict()

    def get(self, key) -> int:
        if key not in self.cache:
            return -1
        self.cache.move_to_end(key)
        return self.cache[key]

    def put(self, key, value) -> None:
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)
```
=== END ===

=== RESPONSE code-flatten ===
```python
def flatten(nested):
    result = []
    for item in nested:
        if isinstance(item, list):
            result.extend(flatten(item))
        else:
            result.append(item)
    return result
```
=== END ===

=== RESPONSE code-kadane ===
```python
def max_subarray(nums: list[int]) -> int:
    best = current = nums[0]
    for num in nums[1:]:
        current = max(num, current + num)
        best = max(best, current)
    return best
```
=== END ===

=== RESPONSE code-bugfix ===
```python
def count_words(text):
    return len(text.split())
```
=== END ===

=== RESPONSE math-change ===
Cost of the pencils: 12 × $0.35 = $4.20.
Change from a $5 bill: $5.00 − $4.20 = $0.80.

Answer: 0.80
=== END ===

=== RESPONSE math-distance ===
First leg: 60 mph × 2.5 h = 150 miles.
Second leg: 80 mph × 1.5 h = 120 miles.
Total: 150 + 120 = 270 miles.

Answer: 270
=== END ===

=== RESPONSE math-gauss ===
The sum of the first n positive integers is n(n + 1)/2.
With n = 50: 50 × 51 / 2 = 2550 / 2 = 1275.

Answer: 1275
=== END ===

=== RESPONSE math-linear ===
3x + 7 = 2x + 19
Subtract 2x from both sides: x + 7 = 19
Subtract 7 from both sides: x = 12

Answer: 12
=== END ===

=== RESPONSE math-rectangle ===
Let the width be w, so the length is 3w.
Perimeter: 2(w + 3w) = 8w = 64, so w = 8.
Length = 3 × 8 = 24.
Area = 24 × 8 = 192.

Answer: 192
=== END ===

=== RESPONSE math-percent ===
Markup of 20%: $250 × 1.20 = $300.
Reduction of 20%: $300 × 0.80 = $240.

Answer: 240
=== END ===

=== RESPONSE math-committee ===
The order of selection does not matter, so we use combinations:
C(8, 3) = 8! / (3! × 5!) = (8 × 7 × 6) / (3 × 2 × 1) = 336 / 6 = 56.

Answer: 56
=== END ===

=== RESPONSE math-squares ===
Let the numbers be a and b with a + b = 30 and ab = 200.
a² + b² = (a + b)² − 2ab = 30² − 2 × 200 = 900 − 400 = 500.

Answer: 500
=== END ===

=== RESPONSE math-remainder ===
7 ≡ 2 (mod 5), so 7^100 ≡ 2^100 (mod 5).
Powers of 2 mod 5 cycle: 2, 4, 3, 1, with period 4.
Since 100 is divisible by 4, 2^100 ≡ (2^4)^25 ≡ 1^25 ≡ 1 (mod 5).

Answer: 1
=== END ===

=== RESPONSE math-dice ===
There are 6 × 6 = 36 equally likely outcomes.
Pairs summing to 7: (1,6), (2,5), (3,4), (4,3), (5,2), (6,1) — 6 outcomes.
Probability = 6/36 = 1/6 ≈ 0.1667, which rounds to 0.167.

Answer: 0.167
=== END ===

=== RESPONSE gen-bullets ===
- Sleep consolidates memory and learning, helping the brain store and organise what you experienced during the day.
- Adequate sleep strengthens the immune system and lowers the risk of heart disease, diabetes and obesity.
- Good sleep improves mood, focus and decision-making, making you more productive and emotionally resilient.
=== END ===

=== RESPONSE gen-lowercase ===
the ocean is a vast, restless body of salt water that covers most of our planet and hides mountains, trenches and countless creatures beneath its shifting surface. its waves rise and fall with the pull of the moon, carrying warmth, weather and life from one shore to another.
=== END ===

=== RESPONSE gen-haiku ===
Crisp leaves drift and fall,
amber light on quiet paths,
the cold wind whispers.
=== END ===

=== RESPONSE gen-json ===
{"city": "Paris", "population": 2100000, "country": "France"}
=== END ===

=== RESPONSE gen-story ===
The river had swallowed the moon by the time Mara reached the bank, her lantern trembling in her hand, its small light searching the black water for the boat that should have come.
=== END ===

=== RESPONSE gen-capital ===
The capital city of Australia is Canberra.
=== END ===

=== RESPONSE gen-author ===
Pride and Prejudice was written by Jane Austen.
=== END ===

=== RESPONSE gen-gold ===
The chemical symbol for gold is Au.
=== END ===

=== RESPONSE gen-logic ===
Yes, because if every bloop is a razzie and every razzie is a lazzie, then by transitivity every bloop must also be a lazzie.
=== END ===

=== RESPONSE gen-order ===
Dave
=== END ===

=== RESPONSE gen-summary ===
Falling solar and battery costs are making renewables the cheapest new electricity, with storage balancing supply at night and accelerating the move away from fossil fuels.
=== END ===

=== RESPONSE gen-translate ===
Buenos días, ¿cómo estás?
=== END ===

=== RESPONSE bench-1 ===
Text generation begins with tokenisation. The input string is split into tokens, usually subword units chosen by an algorithm such as byte-pair encoding, and each token is mapped to an integer ID from a fixed vocabulary. Each ID is looked up in an embedding matrix to produce a vector, and positional information is added so the model knows the order of tokens.

These vectors then pass through a stack of transformer layers. In each layer, self-attention lets every position weigh the other positions in the context, computing queries, keys and values and combining them so that relevant earlier tokens influence the current representation. A feed-forward network then transforms each position independently, with residual connections and layer normalisation keeping training stable. In a decoder-only model, attention is masked so a token can only attend to tokens before it.

At the final layer, the representation of the last position is projected onto the vocabulary, producing one logit per token. A softmax turns these into probabilities. A sampling strategy then picks the next token: greedy decoding takes the most likely one, while temperature, top-k or nucleus sampling introduce controlled randomness. The chosen token is appended to the context and the whole process repeats until a stop token or length limit is reached.
=== END ===

=== RESPONSE bench-2 ===
For thirty-one years Elias Marr had climbed the hundred and twelve steps of the Carrick Point light, and in all that time the sea had given him nothing but weather. So when the morning tide left a green glass bottle wedged between the black rocks below the gallery, he stood over it for a long moment, as though it might be a trick.

The cork came away with a sound like a sigh. Inside, rolled tight and tied with a strip of blue ribbon faded almost to white, was a single sheet of paper. The ink had run at the edges, but the hand was steady and careful.

"To whoever keeps the light at Carrick," it read. "I have watched your beam from the deck of the Isolde every winter crossing for eleven years. It has brought me home more times than I can count. I am retiring now and will not pass this way again. I wanted you to know that someone saw it. Someone was grateful."

There was no name. Elias read it three times, then folded it into the breast pocket of his coat, where it stayed. That night he polished the great lens until it blazed, and he lit it a full hour before dark.
=== END ===

=== RESPONSE bench-3 ===
Movable type was used in China and Korea centuries earlier, but Johannes Gutenberg's press in Mainz around 1450 combined metal type, oil-based ink and a screw press into a system that made printing fast and cheap in Europe. Within fifty years, presses operated in more than two hundred cities and millions of books had been produced, replacing a world in which each copy was laboriously written by hand.

The impact on religion was immediate. Martin Luther's theses and pamphlets spread across Germany within weeks, and vernacular Bibles let ordinary believers read scripture without clerical mediation. The Reformation is hard to imagine without print, and the Catholic Church responded with its own printed catechisms and an Index of forbidden books.

Science was transformed because printing made knowledge stable and cumulative. Identical copies of Copernicus, Vesalius and later Newton could be compared, corrected and built upon by scholars in distant cities, and accurate diagrams, tables and maps could be reproduced exactly.

Politically, print created a reading public. Newspapers, pamphlets and broadsides fuelled the English Civil War, the Enlightenment and the American and French Revolutions, while governments learned to license, censor and tax the press, a struggle that shaped modern ideas of free expression.
=== END ===

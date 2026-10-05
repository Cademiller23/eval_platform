=== RESPONSE coh-essay ===
Why Biodiversity Matters

Biodiversity, the variety of life on Earth from genes to species to entire ecosystems, is the foundation on which human well-being rests. Although we often think of nature as a backdrop to our lives, it is in fact an active partner, providing the air we breathe, the water we drink and the food we eat. Losing that variety weakens the systems that sustain us.

One of the strongest reasons to protect biodiversity is the range of ecosystem services it supports. Bees, bats and birds pollinate crops worth hundreds of billions of dollars each year. Wetlands filter pollutants, forests regulate rainfall and store carbon, and healthy soils depend on countless microorganisms that recycle nutrients. When species disappear, these services can degrade or fail, often at great economic cost.

Diversity also provides resilience. An ecosystem with many species is better able to withstand disease, drought and shifting climates, because if one species declines, others can fill its role. Agriculture illustrates this clearly: crops with narrow genetic bases are vulnerable to blight, while wild relatives hold genes that can protect future harvests. Many of our medicines, too, originate from plants, fungi and animals.

Finally, there are ethical, cultural and aesthetic reasons to value the living world. Many communities find identity and meaning in particular landscapes and species, and countless people draw joy and calm from nature. Protecting biodiversity is therefore not only a practical necessity but also a responsibility to future generations, who deserve to inherit a planet as rich and alive as the one we received.
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
Japanese: こんにちは (Konnichiwa)
Hindi: नमस्ते (Namaste)
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
    for j, n in enumerate(nums):
        need = target - n
        if need in seen:
            return [seen[need], j]
        seen[n] = j
    return []
```
=== END ===

=== RESPONSE code-intervals ===
```python
def merge_intervals(intervals: list[list[int]]) -> list[list[int]]:
    if not intervals:
        return []
    result = []
    for start, end in sorted(intervals, key=lambda x: x[0]):
        if result and start <= result[-1][1]:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result
```
=== END ===

=== RESPONSE code-roman ===
```python
def roman_to_int(s: str) -> int:
    values = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
    total = 0
    for i, ch in enumerate(s):
        v = values[ch]
        if i + 1 < len(s) and v < values[s[i + 1]]:
            total -= v
        else:
            total += v
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
    for n in nums[1:]:
        current = max(n, current + n)
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
Step 1: Cost of pencils = 12 × $0.35 = $4.20.
Step 2: Change = $5.00 − $4.20 = $0.80.

Answer: 0.8
=== END ===

=== RESPONSE math-distance ===
Step 1: First leg = 60 × 2.5 = 150 miles.
Step 2: Second leg = 80 × 1.5 = 120 miles.
Step 3: Total = 150 + 120 = 270 miles.

Answer: 270
=== END ===

=== RESPONSE math-gauss ===
Use the formula n(n + 1)/2 with n = 50.
Sum = 50 × 51 / 2 = 2550 / 2 = 1275.

Answer: 1275
=== END ===

=== RESPONSE math-linear ===
Start with 3x + 7 = 2x + 19.
Subtract 2x from both sides: x + 7 = 19.
Subtract 7 from both sides: x = 12.

Answer: 12
=== END ===

=== RESPONSE math-rectangle ===
Let the width be w, so the length is 3w.
Perimeter = 2(w + 3w) = 8w = 64, so w = 8.
Length = 24.
Area = 8 × 24 = 192.

Answer: 192
=== END ===

=== RESPONSE math-percent ===
Step 1: Markup of 20%: 250 × 1.20 = 300.
Step 2: Reduce by 20%: 300 × 0.80 = 240.

Answer: 240
=== END ===

=== RESPONSE math-committee ===
The order doesn't matter, so use combinations: C(8, 3) = 8! / (3! · 5!) = (8 × 7 × 6) / (3 × 2 × 1) = 336 / 6 = 56.

Answer: 56
=== END ===

=== RESPONSE math-squares ===
Let the numbers be a and b with a + b = 30 and ab = 200.
a² + b² = (a + b)² − 2ab = 900 − 400 = 500.

Answer: 500
=== END ===

=== RESPONSE math-remainder ===
Since 7 ≡ 2 (mod 5), we have 7^100 ≡ 2^100 (mod 5).
The powers of 2 mod 5 cycle with period 4: 2, 4, 3, 1.
100 is divisible by 4, so 2^100 ≡ 1 (mod 5).

Answer: 1
=== END ===

=== RESPONSE math-dice ===
Step 1: There are 6 × 6 = 36 equally likely outcomes.
Step 2: Sum of 7 occurs for (1,6), (2,5), (3,4), (4,3), (5,2), (6,1), which is 6 outcomes.
Step 3: Probability = 6/36 = 1/6 ≈ 0.167.

Answer: 0.167
=== END ===

=== RESPONSE gen-bullets ===
- Sleep strengthens memory and helps the brain learn and focus more effectively.
- Good sleep supports physical health by boosting immunity and aiding heart health.
- Adequate rest improves mood and emotional resilience, reducing stress and irritability.
=== END ===

=== RESPONSE gen-lowercase ===
the ocean is a vast, shimmering expanse of salt water that covers most of our planet and hides countless creatures in its depths. its waves rise and fall with the wind and tides, sounding like a deep and endless breath.
=== END ===

=== RESPONSE gen-haiku ===
Crisp leaves drift and fall,
amber light on quiet fields,
the cool wind whispers.
=== END ===

=== RESPONSE gen-json ===
{"city": "Paris", "population": 2100000, "country": "France"}
=== END ===

=== RESPONSE gen-story ===
Mara lit the old lantern and walked to the edge of the river, where the mist was thick and silent. Something glimmered in the reeds, and she knew, before reaching for it, that her night had changed.
=== END ===

=== RESPONSE gen-capital ===
The capital city of Australia is Canberra.
=== END ===

=== RESPONSE gen-author ===
Jane Austen wrote the novel "Pride and Prejudice."
=== END ===

=== RESPONSE gen-gold ===
The chemical symbol for gold is Au.
=== END ===

=== RESPONSE gen-logic ===
Yes, because if every bloop is a razzie and every razzie is a lazzie, then every bloop must also be a lazzie by transitivity.
=== END ===

=== RESPONSE gen-order ===
Dave
=== END ===

=== RESPONSE gen-summary ===
Though renewable energy, especially solar, has become far cheaper, its intermittency means grids increasingly depend on batteries, and falling battery prices are expected to speed the move away from fossil fuels.
=== END ===

=== RESPONSE gen-translate ===
Buenos días, ¿cómo estás?
=== END ===

=== RESPONSE bench-1 ===
A transformer language model begins with tokenisation, where text is split into subword units, such as those produced by byte-pair encoding, and each token is mapped to an integer ID. These IDs index into an embedding matrix, producing vectors that are combined with positional information so the model knows word order. The sequence then passes through a stack of identical layers. In each layer, self-attention lets every token compute queries, keys and values, weigh the relevance of earlier tokens, and gather information from them; a causal mask prevents looking ahead. The result goes through a feed-forward network, with residual connections and layer normalisation keeping training stable. After the final layer, a linear projection turns the last position's hidden state into logits, one score for every token in the vocabulary. A softmax converts these into probabilities. Sampling then chooses the next token: greedy decoding takes the most likely one, while temperature, top-k and top-p sampling add controlled randomness to vary the output. The chosen token is appended to the input, and the process repeats, with cached keys and values making each step faster, until an end-of-sequence token appears or a length limit is reached. Finally, the generated IDs are decoded back into readable text.
=== END ===

=== RESPONSE bench-2 ===
Elias had kept the Carrow Point light for thirty winters, and the sea had long ago stopped surprising him. Then, one grey dawn after a night of howling wind, he climbed down the slick rocks to check the rope and saw a green glint wedged between two boulders. A bottle, crusted with salt, its cork sealed with candle wax. His hands trembled as he worked it free. Inside lay a single page, brittle and curling. "To whoever finds this," it read, in a careful, slanted hand. "My ship is lost, but I saw your light, and it was the last kind thing I saw. Thank you for staying awake." The date was forty years old. Elias sat on the cold stone while gulls wheeled overhead, and the tide pulled at his boots. He thought of all the nights he had wondered whether anyone noticed the beam sweeping across the dark. He folded the letter against his chest and climbed the stairs slowly. That evening, when he lit the lamp, the light seemed to burn a little warmer, and he kept watch till morning.
=== END ===

=== RESPONSE bench-3 ===
The printing press, introduced around 1440 by Johannes Gutenberg in Mainz, combined movable metal type, oil-based ink and a screw press adapted from wine making. Books that once took months to copy by hand could now be produced in quantity, and prices fell sharply. By 1500, millions of volumes had circulated across Europe. In science, printing allowed accurate texts, diagrams and data to be shared widely and compared. Works like Vesalius's anatomy and Copernicus's astronomy reached readers far beyond their authors' circles, and scholars could build on one another's findings, supporting the Scientific Revolution. In religion, the press made Bibles and pamphlets cheap, helping Martin Luther's ideas spread rapidly in 1517 and fuelling the Reformation. Literacy rose as people read scripture in their own languages, weakening the Church's control over interpretation. In politics, printed pamphlets, newspapers and broadsheets created a public sphere in which citizens debated policy and challenged authority. Rulers responded with licensing and censorship, yet ideas still travelled, shaping movements from the English Civil War to the American and French Revolutions. Overall, the press transformed knowledge from a scarce possession of elites into a shared resource, reshaping how societies learn, believe and govern themselves.
=== END ===

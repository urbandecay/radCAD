# Ortho Snap Logic — Current vs Proposed

## Current Behavior (The Problem)

Every frame, the code does this:
1. Find cells near the cursor (or ALL cells in ortho)
2. **For every cell in that list, loop through every vert**
3. Check if vert projects to screen space and is within snap radius
4. Store candidates

Problem: Even if only 1 cell is "active" (mouse is in it), the code still iterates all verts in all nearby cells. If a distant cell has a million verts, you waste time checking all of them every frame, even though you're not near that cell.

## Your Logic (The Fix)

**Grid cells are built once and cached.** They turn yellow and never change unless the mesh changes.

Every frame:
1. **Only check which cell(s) the mouse is actually in** (instant check, fractions of millisecond)
2. **Only iterate verts in those active cells**
3. Distant cells stay yellow (cached, known) but **never get searched**

Result: You're not wasting time iterating verts in cells you're not even near.

Example: 43 cells total. Mouse in cell 5. Only iterate verts in cell 5. Don't touch cells 1-4, 6-43. Their verts aren't checked.

## How to Fix It

Instead of:
```python
for ck in nearby_cells:  # ALL cells (or ray-marched cells)
    for wco in cell["verts"]:  # iterate EVERY vert every frame
        # check snap
```

Do:
```python
# Figure out which cell(s) the mouse is in
active_cells = find_cells_containing_mouse()

# Only iterate verts in those cells
for ck in active_cells:
    for wco in cell["verts"]:
        # check snap
```

The grid building stays the same. The projection logic stays the same. **Just don't iterate verts in cells you're not in.**

---

## Does This Logic Make Sense?

Does it sound right? If yes, I'll implement it.

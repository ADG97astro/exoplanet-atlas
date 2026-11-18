import json, os
d = json.load(open("data/papers.json", encoding="utf-8"))
lines = ["papers: %d, file size: %.1f MB" % (len(d["papers"]), os.path.getsize("data/papers.json") / 1e6)]
for c in d["clusters"]:
    lines.append("  " * (c["depth"] - 1) + c["id"] + "  (" + str(c["n"]) + ")  " + c["name"])
open("tree.txt", "w", encoding="utf-8").write("\n".join(lines))
print("wrote tree.txt with", len(lines) - 1, "subfields")

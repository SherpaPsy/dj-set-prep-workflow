from mutagen.id3 import ID3NoHeaderError, ID3, TIT2, TPE1

path = r"C:\Users\sherp\OneDrive\Music\DJ-Set-Prep\ProcessedAIFF\Azee_Project-Raise-Main_Mix-78594226.aif"

try:
    tags = ID3(path)
    print(f"ID3 tags exist: {len(tags)} frames")
    for key, value in tags.items():
        print(f"  {key}: {value}")
except ID3NoHeaderError:
    print("No ID3 header found")
except Exception as e:
    print(f"Error: {type(e).__name__}: {e}")

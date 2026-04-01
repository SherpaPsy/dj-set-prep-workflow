from mutagen.id3 import ID3

path = r"C:\Users\sherp\OneDrive\Music\DJ-Set-Prep\ProcessedWAV\Azee_Project-Raise-Main_Mix-78594226.wav"

try:
    tags = ID3(path)
    print(f"ID3 tags exist: {len(tags)} frames")
    for key, value in tags.items():
        print(f"  {key}: {value}")
except Exception as e:
    print(f"Error: {type(e).__name__}: {e}")

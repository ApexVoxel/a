const express = require('express');
const multer = require('multer');
const path = require('path');
const fs = require('fs-extra');
const cors = require('cors');
const { v4: uuidv4 } = require('uuid');

const app = express();
const PORT = process.env.PORT || 3000;

// Middleware
app.use(cors());
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// Ensure uploads & data folders exist
const UPLOADS_DIR = path.join(__dirname, 'uploads');
const DATA_DIR = path.join(__dirname, 'data');
const METADATA_FILE = path.join(DATA_DIR, 'media.json');

fs.ensureDirSync(UPLOADS_DIR);
fs.ensureDirSync(DATA_DIR);

// Load or initialise metadata database
let mediaDB = [];
if (fs.existsSync(METADATA_FILE)) {
  mediaDB = fs.readJsonSync(METADATA_FILE);
} else {
  mediaDB = [];
  fs.writeJsonSync(METADATA_FILE, mediaDB);
}

// Helper: save DB to disk
function saveDB() {
  fs.writeJsonSync(METADATA_FILE, mediaDB);
}

// Configure multer for unlimited file size, any number of files
const storage = multer.diskStorage({
  destination: (req, file, cb) => cb(null, UPLOADS_DIR),
  filename: (req, file, cb) => {
    const ext = path.extname(file.originalname);
    const uniqueName = `${uuidv4()}${ext}`;
    cb(null, uniqueName);
  }
});
const upload = multer({
  storage,
  limits: { fileSize: Infinity },  // no size limit
}).array('files', 100);  // allow up to 100 files per request

// Serve static files (frontend HTML + uploaded media)
app.use(express.static(__dirname));        // serves index.html (the frontend)
app.use('/uploads', express.static(UPLOADS_DIR));

// ---------- API endpoints ----------
// GET all media
app.get('/api/media', (req, res) => {
  res.json(mediaDB);
});

// POST upload multiple files
app.post('/upload', (req, res) => {
  upload(req, res, async (err) => {
    if (err) {
      return res.status(400).json({ error: err.message });
    }
    if (!req.files || req.files.length === 0) {
      return res.status(400).json({ error: 'No files uploaded' });
    }

    const newEntries = [];
    for (const file of req.files) {
      const mimeType = file.mimetype;
      const isImage = mimeType.startsWith('image/');
      const isVideo = mimeType.startsWith('video/');
      if (!isImage && !isVideo) {
        // skip non-media (should not happen due to accept attr, but safety)
        fs.unlinkSync(file.path);
        continue;
      }
      const mediaItem = {
        id: uuidv4(),
        filename: file.filename,
        originalName: file.originalname,
        mimeType: mimeType,
        size: file.size,
        title: path.parse(file.originalname).name,
        description: '',
        uploadDate: new Date().toISOString()
      };
      mediaDB.unshift(mediaItem);  // newest first
      newEntries.push(mediaItem);
    }
    saveDB();
    res.json({ uploaded: newEntries.length, errors: 0 });
  });
});

// PUT edit media metadata
app.put('/api/media/:id', (req, res) => {
  const { id } = req.params;
  const { title, description } = req.body;
  const index = mediaDB.findIndex(m => m.id === id);
  if (index === -1) {
    return res.status(404).json({ error: 'Media not found' });
  }
  if (title !== undefined) mediaDB[index].title = title;
  if (description !== undefined) mediaDB[index].description = description;
  saveDB();
  res.json(mediaDB[index]);
});

// (Optional) DELETE is NOT implemented – files never get deleted.
// If you want to be extra safe, do not add a delete route.

// Serve the frontend HTML (the one I gave you) as index.html
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'index.html'));
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`Hydra Nude server running on port ${PORT}`);
});

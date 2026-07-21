import { useState } from "react";

export default function ResumeUploader() {
  const [file, setFile] = useState<File | null>(null);
  const [jobs, setJobs] = useState<any[]>([]);
  const [message, setMessage] = useState("");
  const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined) || "http://127.0.0.1:8002";

  const uploadResume = async (selectedFile: File) => {
    const formData = new FormData();
    formData.append("file", selectedFile);

    const res = await fetch(`${apiBaseUrl}/upload-resume`, {
      method: "POST",
      body: formData,
    });

    if (!res.ok) {
      const error = await res.json();
      throw new Error(error.detail || "Upload failed");
    }

    return res.json();
  };

  const fetchJobs = async () => {
    const res = await fetch(`${apiBaseUrl}/api/v1/jobs/sources`);
    if (!res.ok) {
      throw new Error("Failed to fetch jobs");
    }
    return res.json();
  };

  const handleUpload = async () => {
    if (!file) {
      setMessage("Please choose a file first.");
      return;
    }

    try {
      setMessage("Uploading...");
      const uploadResult = await uploadResume(file);
      setMessage(`Uploaded: ${uploadResult.filename}`);
      const jobsResult = await fetchJobs();
      setJobs(jobsResult);
    } catch (err: any) {
      setMessage(err.message || "Something went wrong");
    }
  };

  return (
    <div style={{ maxWidth: 720, margin: "2rem auto", padding: 24 }}>
      <h2>Upload Resume</h2>
      <input
        type="file"
        accept=".pdf,.docx"
        onChange={(e) => setFile(e.target.files?.[0] || null)}
      />
      <button onClick={handleUpload} style={{ marginTop: 12 }}>
        Upload Resume
      </button>

      <p style={{ marginTop: 12 }}>{message}</p>

      <div style={{ marginTop: 24 }}>
        {jobs.map((job, index) => (
          <div key={index} style={{ border: "1px solid #ddd", padding: 16, marginBottom: 12 }}>
            <h3>{job.title}</h3>
            <p><strong>Company:</strong> {job.company}</p>
            <p><strong>Location:</strong> {job.location}</p>
            <p><strong>Source:</strong> {job.source}</p>
            <p>{job.description}</p>
            {job.url ? <a href={job.url} target="_blank" rel="noreferrer">View job</a> : null}
          </div>
        ))}
      </div>
    </div>
  );
}

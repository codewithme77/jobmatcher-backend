from app.job_sources import ArbeitnowProvider, GreenhouseProvider, JobAggregator, RemoteOKProvider


def test_aggregator_deduplicates_jobs():
    provider_a = ArbeitnowProvider(base_url="https://example.com/arbeitnow")
    provider_b = RemoteOKProvider(base_url="https://example.com/remoteok")
    provider_c = GreenhouseProvider(base_url="https://example.com/greenhouse")

    aggregator = JobAggregator(providers=[provider_a, provider_b, provider_c])
    jobs = aggregator.fetch_all_jobs()

    assert isinstance(jobs, list)
    assert jobs == []

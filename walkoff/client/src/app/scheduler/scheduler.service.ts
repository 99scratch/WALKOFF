import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import 'rxjs/add/operator/map';
import 'rxjs/add/operator/toPromise';
import { plainToClass } from 'class-transformer';

import { ScheduledTask } from '../models/scheduler/scheduledTask';
import { Playbook } from '../models/playbook/playbook';
import { UtilitiesService } from '../utilities.service';

const schedulerStatusNumberMapping: { [key: number]: string } = {
	0: 'stopped',
	1: 'running',
	2: 'paused',
};

@Injectable()
export class SchedulerService {
	constructor (private http: HttpClient, private utils: UtilitiesService) {}

	getSchedulerStatus(): Promise<string> {
		return this.http.get('/walkoff/api/scheduler')
			.toPromise()
			.then((statusObj: any) => schedulerStatusNumberMapping[statusObj.status])
			.catch(this.utils.handleResponseError);
	}

	changeSchedulerStatus(status: string): Promise<string> {
		return this.http.put('/walkoff/api/scheduler', { status })
			.toPromise()
			.then((statusObj: any) => schedulerStatusNumberMapping[statusObj.status])
			.catch(this.utils.handleResponseError);
	}

	getAllScheduledTasks(): Promise<ScheduledTask[]> {
		return this.utils.paginateAll<ScheduledTask>(this.getScheduledTasks.bind(this));
	}

	getScheduledTasks(page: number = 1): Promise<ScheduledTask[]> {
		return this.http.get(`/walkoff/api/scheduledtasks?page=${ page }`)
			.toPromise()
			.then((data: object[]) => plainToClass(ScheduledTask, data))
			.catch(this.utils.handleResponseError);
	}

	addScheduledTask(scheduledTask: ScheduledTask): Promise<ScheduledTask> {
		return this.http.post('/walkoff/api/scheduledtasks', scheduledTask)
			.toPromise()
			.then((data: object) => plainToClass(ScheduledTask, data))
			.catch(this.utils.handleResponseError);
	}

	editScheduledTask(scheduledTask: ScheduledTask): Promise<ScheduledTask> {
		return this.http.put('/walkoff/api/scheduledtasks', scheduledTask)
			.toPromise()
			.then((data: object) => plainToClass(ScheduledTask, data))
			.catch(this.utils.handleResponseError);
	}

	deleteScheduledTask(scheduledTaskId: number): Promise<void> {
		return this.http.delete(`/walkoff/api/scheduledtasks/${scheduledTaskId}`)
			.toPromise()
			.then(() => null)
			.catch(this.utils.handleResponseError);
	}

	changeScheduledTaskStatus(scheduledTaskId: number, actionName: string): Promise<void> {
		return this.http.patch('/walkoff/api/scheduledtasks', { id: scheduledTaskId, action: actionName })
			.toPromise()
			.then(() => null)
			.catch(this.utils.handleResponseError);
	}

	getPlaybooks(): Promise<Playbook[]> {
		return this.http.get('/walkoff/api/playbooks')
			.toPromise()
			.then((data: object[]) => plainToClass(Playbook, data))
			.catch(this.utils.handleResponseError);
	}
}
